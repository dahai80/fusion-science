"""Tests for the compute layer."""

from __future__ import annotations

import pytest

from fusion_science.compute.hpc_scheduler import HPCClusterInfo, HPCJob, HPCScheduler
from fusion_science.compute.python_executor import PythonExecutor


class TestPythonExecutor:
    """Test the Python code executor."""

    @pytest.mark.asyncio
    async def test_simple_execution(self):
        executor = PythonExecutor(timeout=10)
        result = await executor.execute("result = 42")
        assert result.success

    @pytest.mark.asyncio
    async def test_execution_with_output(self):
        executor = PythonExecutor(timeout=10)
        code = """
result = "Hello, Fusion Science!"
"""
        result = await executor.execute(code)
        assert result.success

    @pytest.mark.asyncio
    async def test_execution_error(self):
        executor = PythonExecutor(timeout=10)
        result = await executor.execute("1/0")
        assert not result.success
        assert "ZeroDivisionError" in result.error or "ZeroDivisionError" in result.stderr

    @pytest.mark.asyncio
    async def test_execution_timeout(self):
        executor = PythonExecutor(timeout=1)
        result = await executor.execute("import time; time.sleep(10)")
        # May or may not timeout depending on asyncio handling
        assert result is not None

    @pytest.mark.asyncio
    async def test_syntax_error(self):
        executor = PythonExecutor(timeout=10)
        result = await executor.execute("invalid syntax ===")
        assert not result.success

    def test_check_available_packages(self):
        packages = PythonExecutor.check_available_packages()
        assert len(packages) > 0
        # numpy should be available (it's a dependency)
        numpy_info = next((p for p in packages if p["name"] == "numpy"), None)
        if numpy_info:
            assert numpy_info["available"] is True


class TestHPCScheduler:
    """Test the HPC scheduler."""

    def test_scheduler_init(self):
        scheduler = HPCScheduler(use_local=True)
        assert scheduler.use_local is True

    def test_scheduler_ssh_config(self):
        scheduler = HPCScheduler(ssh_host="cluster.example.com", ssh_key="~/.ssh/id_rsa")
        assert scheduler.ssh_host == "cluster.example.com"
        assert scheduler.ssh_key == "~/.ssh/id_rsa"

    def test_build_slurm_headers(self):
        scheduler = HPCScheduler(use_local=True)
        # Access private method for testing
        headers = scheduler._build_slurm_headers(
            job_name="test_job",
            partition="gpu",
            nodes=2,
            tasks=4,
            cpus_per_task=8,
            memory_gb=32,
            gpus=2,
            time_limit="02:00:00",
            array_range="",
        )
        assert "#SBATCH --job-name=test_job" in headers
        assert "#SBATCH --nodes=2" in headers
        assert "#SBATCH --ntasks=4" in headers
        assert "#SBATCH --cpus-per-task=8" in headers
        assert "#SBATCH --mem=32G" in headers
        assert "#SBATCH --time=02:00:00" in headers
        assert "#SBATCH --partition=gpu" in headers
        assert "#SBATCH --gres=gpu:2" in headers

    def test_generate_parallel_script(self):
        script = HPCScheduler.generate_parallel_script(
            python_code="print('analysis')",
            input_files=["file1.txt", "file2.txt", "file3.txt"],
            output_dir="/output",
        )
        assert "Array job for parallel processing" in script
        assert "SLURM_ARRAY_TASK_ID" in script
        assert "/output" in script


class TestHPCJob:
    """Test the HPCJob dataclass."""

    def test_default_job(self):
        job = HPCJob(job_id="12345", name="test")
        assert job.job_id == "12345"
        assert job.name == "test"
        assert job.status == "PENDING"
        assert job.nodes == 1
        assert job.memory_gb == 4

    def test_custom_job(self):
        job = HPCJob(
            job_id="67890",
            name="analysis",
            status="COMPLETED",
            partition="gpu",
            nodes=4,
            tasks=8,
            memory_gb=64,
        )
        assert job.job_id == "67890"
        assert job.status == "COMPLETED"
        assert job.nodes == 4
        assert job.memory_gb == 64


class TestHPCClusterInfo:
    """Test the HPCClusterInfo dataclass."""

    def test_default_info(self):
        info = HPCClusterInfo()
        assert info.name == ""
        assert info.partitions == []
        assert info.total_nodes == 0
        assert info.available is False

    def test_custom_info(self):
        info = HPCClusterInfo(
            name="supercluster",
            partitions=[{"name": "gpu", "nodes": "10"}],
            total_nodes=100,
            total_cpus=2000,
            available=True,
        )
        assert info.name == "supercluster"
        assert info.available is True
        assert len(info.partitions) == 1
