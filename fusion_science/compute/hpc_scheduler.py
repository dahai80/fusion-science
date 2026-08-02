"""HPC/Slurm scheduler connector — submit and monitor compute jobs on clusters.

Provides an interface for scheduling computational experiments on
HPC clusters with Slurm workload manager, supporting:
- Job submission with resource specifications
- Job status monitoring
- Output retrieval
- Array jobs for parallel parameter sweeps
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class HPCJob:
    """Represents a submitted HPC job."""

    job_id: str
    name: str
    status: str = "PENDING"  # PENDING, RUNNING, COMPLETED, FAILED, CANCELLED
    partition: str = ""
    nodes: int = 1
    tasks: int = 1
    memory_gb: int = 4
    time_limit: str = "01:00:00"
    script_path: str = ""
    output_path: str = ""
    error_path: str = ""
    submitted_at: str = ""


@dataclass
class HPCClusterInfo:
    """Information about the HPC cluster."""

    name: str = ""
    partitions: list[dict[str, Any]] = field(default_factory=list)
    total_nodes: int = 0
    total_cpus: int = 0
    total_memory_gb: int = 0
    available: bool = False


class HPCScheduler:
    """Interface for HPC cluster job scheduling (Slurm).

    Supports local Slurm, SSH-remote Slurm, and fallback to
    local subprocess execution for non-HPC environments.
    """

    def __init__(
        self,
        use_local: bool = False,
        ssh_host: str = "",
        ssh_key: str = "",
        slurm_partition: str = "",
        slurm_account: str = "",
    ):
        self.use_local = use_local
        self.ssh_host = ssh_host
        self.ssh_key = ssh_key
        self.slurm_partition = slurm_partition
        self.slurm_account = slurm_account
        self._sbatch_available: bool | None = None
        self._check_sbatch()

    def _check_sbatch(self) -> bool:
        """Check if Slurm sbatch is available."""
        if self._sbatch_available is not None:
            return self._sbatch_available
        try:
            subprocess.run(
                ["which", "sbatch"],
                capture_output=True, timeout=5,
                check=False,
            )
            self._sbatch_available = True
        except Exception:
            self._sbatch_available = False
        return self._sbatch_available

    async def submit_job(
        self,
        script_content: str,
        job_name: str = "fusion_science",
        partition: str = "",
        nodes: int = 1,
        tasks: int = 1,
        cpus_per_task: int = 1,
        memory_gb: int = 4,
        gpus: int = 0,
        time_limit: str = "01:00:00",
        array_range: str = "",
        env_vars: dict[str, str] | None = None,
    ) -> HPCJob:
        """Submit a job to the HPC cluster.

        Args:
            script_content: The job script content (without Slurm headers).
            job_name: Name for the job.
            partition: Slurm partition name.
            nodes: Number of nodes.
            tasks: Number of tasks.
            cpus_per_task: CPUs per task.
            memory_gb: Memory in GB.
            gpus: Number of GPUs.
            time_limit: Time limit (HH:MM:SS).
            array_range: Array job range (e.g., "1-10").
            env_vars: Additional environment variables.

        Returns:
            HPCJob with job_id and status.
        """
        # Build the complete Slurm script
        slurm_headers = self._build_slurm_headers(
            job_name=job_name,
            partition=partition or self.slurm_partition,
            nodes=nodes,
            tasks=tasks,
            cpus_per_task=cpus_per_task,
            memory_gb=memory_gb,
            gpus=gpus,
            time_limit=time_limit,
            array_range=array_range,
        )

        # Add environment variables
        env_section = ""
        if env_vars:
            env_section = "\n".join(f"export {k}={v}" for k, v in env_vars.items())

        # If Slurm is not available, run locally
        if not self._check_sbatch() and not self.ssh_host:
            return await self._run_locally(
                script_content, job_name, memory_gb, time_limit
            )

        # Write the script to a temp file
        full_script = f"{slurm_headers}\n{env_section}\n{script_content}"
        script_path = self._write_script(full_script, job_name)

        try:
            if self.ssh_host:
                # Submit via SSH
                job_id = await self._submit_ssh(script_path)
            else:
                # Submit locally
                result = subprocess.run(
                    ["sbatch", script_path],
                    capture_output=True, text=True, timeout=30,
                    check=False,
                )
                if result.returncode != 0:
                    raise RuntimeError(f"sbatch failed: {result.stderr}")
                # Parse job ID from "Submitted batch job 123456"
                match = re.search(r"Submitted batch job (\d+)", result.stdout)
                job_id = match.group(1) if match else "unknown"

            return HPCJob(
                job_id=job_id,
                name=job_name,
                status="PENDING",
                partition=partition or self.slurm_partition,
                nodes=nodes,
                tasks=tasks,
                memory_gb=memory_gb,
                time_limit=time_limit,
                script_path=script_path,
            )

        except Exception as e:
            logger.error("Job submission failed: %s", e)
            return await self._run_locally(
                script_content, job_name, memory_gb, time_limit
            )

    def _build_slurm_headers(
        self,
        job_name: str,
        partition: str,
        nodes: int,
        tasks: int,
        cpus_per_task: int,
        memory_gb: int,
        gpus: int,
        time_limit: str,
        array_range: str,
    ) -> str:
        """Build Slurm #SBATCH headers."""
        headers = [
            "#!/bin/bash",
            f"#SBATCH --job-name={job_name}",
            f"#SBATCH --nodes={nodes}",
            f"#SBATCH --ntasks={tasks}",
            f"#SBATCH --cpus-per-task={cpus_per_task}",
            f"#SBATCH --mem={memory_gb}G",
            f"#SBATCH --time={time_limit}",
        ]

        if partition:
            headers.append(f"#SBATCH --partition={partition}")
        if self.slurm_account:
            headers.append(f"#SBATCH --account={self.slurm_account}")
        if gpus > 0:
            headers.append(f"#SBATCH --gres=gpu:{gpus}")
        if array_range:
            headers.append(f"#SBATCH --array={array_range}")

        # Output files
        headers.append(f"#SBATCH --output=logs/{job_name}_%j.out")
        headers.append(f"#SBATCH --error=logs/{job_name}_%j.err")

        return "\n".join(headers)

    def _write_script(self, content: str, job_name: str) -> str:
        """Write the job script to a temporary file."""
        script_dir = os.path.join(tempfile.gettempdir(), "fusion_science_jobs")
        os.makedirs(script_dir, exist_ok=True)
        script_path = os.path.join(script_dir, f"{job_name}.sh")
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(content)
        os.chmod(script_path, 0o755)
        return script_path

    async def _run_locally(
        self,
        script_content: str,
        job_name: str,
        memory_gb: int,
        time_limit: str,
    ) -> HPCJob:
        """Run the job locally as a subprocess (fallback)."""
        import time
        job_id = f"local_{int(time.time())}"

        log_dir = os.path.join(tempfile.gettempdir(), "fusion_science_jobs", "logs")
        os.makedirs(log_dir, exist_ok=True)
        out_path = os.path.join(log_dir, f"{job_name}_{job_id}.out")
        err_path = os.path.join(log_dir, f"{job_name}_{job_id}.err")

        # Write and execute the script
        script_path = self._write_script(script_content, f"{job_name}_{job_id}")

        # Run in background with async-safe file handles
        out_fd = await asyncio.to_thread(lambda: open(out_path, "w"))  # noqa: SIM115
        err_fd = await asyncio.to_thread(lambda: open(err_path, "w"))  # noqa: SIM115
        proc = await asyncio.create_subprocess_exec(
            "/bin/bash", script_path,
            stdout=out_fd,
            stderr=err_fd,
        )

        logger.info("Local job %s started (PID: %s)", job_id, proc.pid)
        return HPCJob(
            job_id=job_id,
            name=job_name,
            status="RUNNING",
            script_path=script_path,
            output_path=out_path,
            error_path=err_path,
        )

    async def _submit_ssh(self, script_path: str) -> str:
        """Submit a job via SSH to a remote cluster."""
        cmd = ["ssh", "-i", self.ssh_key, self.ssh_host, f"sbatch {script_path}"]
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=30, check=False
        )
        if result.returncode != 0:
            raise RuntimeError(f"SSH sbatch failed: {result.stderr}")
        match = re.search(r"Submitted batch job (\d+)", result.stdout)
        return match.group(1) if match else "unknown"

    async def check_status(self, job_id: str) -> str:
        """Check the status of a submitted job.

        Args:
            job_id: Job ID to check.

        Returns:
            Status string (PENDING, RUNNING, COMPLETED, FAILED, etc.).
        """
        if job_id.startswith("local_"):
            # Local jobs: check if process is still running
            return "RUNNING"  # Simplified; actual status check would need PID tracking

        try:
            result = subprocess.run(
                ["sacct", "-j", job_id, "--format=State", "--noheader", "--parsable2"],
                capture_output=True, text=True, timeout=30, check=False,
            )
            if result.returncode == 0:
                state = result.stdout.strip().split("\n")[0] if result.stdout.strip() else "UNKNOWN"
                return state
            # Fallback to squeue
            result = subprocess.run(
                ["squeue", "-j", job_id, "--noheader", "--format=%T"],
                capture_output=True, text=True, timeout=30, check=False,
            )
            return result.stdout.strip() or "UNKNOWN"
        except Exception as e:
            logger.warning("Failed to check job status: %s", e)
            return "UNKNOWN"

    async def cancel_job(self, job_id: str) -> bool:
        """Cancel a submitted job.

        Args:
            job_id: Job ID to cancel.

        Returns:
            True if cancelled successfully.
        """
        if job_id.startswith("local_"):
            logger.info("Cannot cancel local job %s via Slurm", job_id)
            return False

        try:
            result = subprocess.run(
                ["scancel", job_id],
                capture_output=True, text=True, timeout=30, check=False,
            )
            return result.returncode == 0
        except Exception as e:
            logger.error("Failed to cancel job %s: %s", job_id, e)
            return False

    async def get_cluster_info(self) -> HPCClusterInfo:
        """Get information about the HPC cluster.

        Returns:
            HPCClusterInfo with cluster details.
        """
        info = HPCClusterInfo()
        info.available = self._check_sbatch()

        if not info.available:
            return info

        try:
            # Get partition info
            result = subprocess.run(
                ["sinfo", "--format=%P|%D|%c|%m|%e", "--noheader"],
                capture_output=True, text=True, timeout=30, check=False,
            )
            if result.returncode == 0:
                for line in result.stdout.strip().split("\n"):
                    parts = line.split("|")
                    if len(parts) >= 5:
                        info.partitions.append({
                            "name": parts[0].strip(),
                            "nodes": parts[1].strip(),
                            "cpus": parts[2].strip(),
                            "memory": parts[3].strip(),
                            "free": parts[4].strip(),
                        })
        except Exception as e:
            logger.warning("Failed to get cluster info: %s", e)

        return info

    async def get_job_output(self, job_id: str, job_name: str = "") -> str:
        """Get the output of a completed job.

        Args:
            job_id: Job ID.
            job_name: Job name (for local jobs).

        Returns:
            Job output as a string.
        """
        log_dir = os.path.join(tempfile.gettempdir(), "fusion_science_jobs", "logs")
        # Try standard Slurm output pattern
        out_path = os.path.join(log_dir, f"{job_name}_{job_id}.out")
        if os.path.exists(out_path):
            with open(out_path, encoding="utf-8", errors="replace") as f:
                return f.read()
        return ""

    @staticmethod
    def generate_parallel_script(
        python_code: str,
        input_files: list[str],
        output_dir: str,
    ) -> str:
        """Generate a Slurm array job script for parallel processing.

        Args:
            python_code: Python code to execute (uses $SLURM_ARRAY_TASK_ID).
            input_files: List of input files (one per array task).
            output_dir: Output directory.

        Returns:
            Complete Slurm script as a string.
        """
        array_size = len(input_files)
        script = f"""#!/bin/bash
# Array job for parallel processing
# {array_size} tasks

# Get the input file for this task
INPUT_FILE="{input_files[0]}"
INPUT_FILE=${{INPUT_FILE//1/$SLURM_ARRAY_TASK_ID}}
OUTPUT_DIR="{output_dir}"
mkdir -p $OUTPUT_DIR

# Execute the analysis
{python_code}

echo "Task $SLURM_ARRAY_TASK_ID complete"
"""
        return script
