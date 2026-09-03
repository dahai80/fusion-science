"""Python code executor — sandboxed execution of Python code for scientific analysis.

Executes user-generated Python code in a controlled subprocess with
timeout, resource limits, and output capture. Supports matplotlib
figure generation and numpy/pandas/scipy-based analysis.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import sys
import tempfile
import textwrap
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    """Result of a code execution."""

    success: bool
    stdout: str = ""
    stderr: str = ""
    output: str = ""  # Combined stdout + stderr
    error: str = ""
    execution_time: float = 0.0
    figures: list[str] = field(default_factory=list)  # Paths to generated figures


class PythonExecutor:
    """Sandboxed Python code executor for scientific analysis.

    Executes code in a subprocess with configurable timeout and
    resource limits. Supports matplotlib figure capture.
    """

    def __init__(
        self,
        timeout: int = 120,
        work_dir: str | None = None,
        extra_paths: list[str] | None = None,
        sandbox: Any | None = None,
    ):
        self.timeout = timeout
        self.work_dir = work_dir or tempfile.mkdtemp(prefix="fusion_science_")
        self.extra_paths = extra_paths or []
        self._figure_counter = 0
        # P0 (S2): SandboxManager AST validation. A shared instance is injected
        # at construction; if absent a default SandboxManager is created so the
        # dangerous-import / eval / subprocess guards always run before execution.
        if sandbox is None:
            from .sandbox import SandboxManager

            sandbox = SandboxManager()
        self._sandbox = sandbox

    async def execute(
        self,
        code: str,
        input_data: dict[str, Any] | None = None,
        env_vars: dict[str, str] | None = None,
        capture_figures: bool = True,
    ) -> ExecutionResult:
        """Execute Python code in a sandboxed subprocess.

        Args:
            code: Python code to execute.
            input_data: Optional input data to pass to the code (serialized as JSON).
            env_vars: Optional environment variables.
            capture_figures: Whether to capture matplotlib figures.

        Returns:
            ExecutionResult with stdout, stderr, and figure paths.
        """
        start = asyncio.get_event_loop().time()

        # P0 (S2): AST validation gate — reject dangerous imports / eval /
        # subprocess / os.system BEFORE any subprocess is spawned. The rlimit
        # layer (CPU/memory/nproc) is defense-in-depth but cannot stop a
        # `subprocess.run("rm -rf ~")` that completes within the limits.
        validation = self._sandbox.validate_code(code, language="python")
        if not validation.get("valid", True):
            issues = validation.get("issues", [])
            logger.warning("PythonExecutor rejected code: %s", "; ".join(issues))
            return ExecutionResult(
                success=False,
                error=f"Code rejected by sandbox: {'; '.join(issues)}",
                execution_time=0.0,
            )

        # Create a wrapper script that captures output and figures
        script = self._build_wrapper(code, input_data is not None, capture_figures)
        script_path = os.path.join(self.work_dir, f"exec_{int(start)}_{id(self)}.py")
        output_path = os.path.join(self.work_dir, f"output_{int(start)}_{id(self)}.json")
        input_path = os.path.join(self.work_dir, f"input_{int(start)}_{id(self)}.json")

        try:
            with open(script_path, "w", encoding="utf-8") as f:
                f.write(script)

            # input_data written to a temp file (read by wrapper) — never inlined
            # into source, avoids triple-quote / code injection via data content.
            if input_data is not None:
                import json

                with open(input_path, "w", encoding="utf-8") as f:
                    json.dump(input_data, f, ensure_ascii=False)

            # Minimal env whitelist — do NOT copy os.environ (leaks secrets/HOME/PATH-tampering).
            # Only pass what the sandboxed code genuinely needs.
            env = {
                "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                "LANG": os.environ.get("LANG", "en_US.UTF-8"),
                "LC_ALL": os.environ.get("LC_ALL", "en_US.UTF-8"),
                "TMPDIR": os.environ.get("TMPDIR", tempfile.gettempdir()),
                "PYTHONPATH": ":".join(self.extra_paths),
                "FUSION_SCIENCE_OUTPUT": output_path,
            }
            if input_data is not None:
                env["FUSION_SCIENCE_INPUT"] = input_path
            if env_vars:
                # Caller-supplied env_vars are explicitly intended for the sandbox.
                env.update(env_vars)

            # Run the subprocess with resource limits + own process group
            try:
                import resource
            except ImportError:
                resource = None  # Windows / non-UNIX fallback

            def _set_limits():
                """Apply resource limits (CPU, memory, processes) to child process."""
                if resource is None:
                    return
                try:
                    # 30 seconds CPU time
                    resource.setrlimit(resource.RLIMIT_CPU, (30, 30))
                    # 2 GB virtual memory
                    resource.setrlimit(resource.RLIMIT_AS, (2 * 1024 * 1024 * 1024, 2 * 1024 * 1024 * 1024))
                    # 50 child processes max
                    resource.setrlimit(resource.RLIMIT_NPROC, (50, 50))
                except (ValueError, resource.error):
                    pass  # Some limits may not be supported on all platforms

            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                script_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=self.work_dir,
                env=env,
                preexec_fn=_set_limits,
                start_new_session=True,
            )

            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=self.timeout)
            except TimeoutError:
                # Kill the whole process group so orphaned children die too.
                with contextlib.suppress(ProcessLookupError, PermissionError):
                    os.killpg(os.getpgid(proc.pid), 9)
                await proc.wait()
                duration = asyncio.get_event_loop().time() - start
                return ExecutionResult(
                    success=False,
                    stdout="",
                    stderr="",
                    error=f"Execution timed out after {self.timeout}s",
                    execution_time=duration,
                )

            duration = asyncio.get_event_loop().time() - start
            stdout_str = stdout.decode("utf-8", errors="replace") if stdout else ""
            stderr_str = stderr.decode("utf-8", errors="replace") if stderr else ""

            # Parse output file
            figures = []
            parsed_output = ""
            wrapper_success = None
            wrapper_error = ""
            if os.path.exists(output_path):
                try:
                    import json

                    with open(output_path, encoding="utf-8") as f:
                        result_data = json.load(f)
                    parsed_output = result_data.get("output", "")
                    figures = result_data.get("figures", [])
                    wrapper_success = result_data.get("success")
                    wrapper_error = result_data.get("error", "")
                except Exception as e:
                    logger.warning("Failed to parse output file: %s", e)

            # A non-zero returncode means the wrapper itself crashed (import/limit).
            # Otherwise trust the wrapper's per-code success flag so a caught user
            # exception is reported as failure even though the process exited 0.
            if proc.returncode != 0:
                success = False
                error = stderr_str
            elif wrapper_success is not None:
                success = bool(wrapper_success)
                error = wrapper_error if not success else ""
            else:
                success = True
                error = ""
            return ExecutionResult(
                success=success,
                stdout=stdout_str,
                stderr=stderr_str,
                output=parsed_output or stdout_str,
                error=error,
                execution_time=duration,
                figures=figures,
            )

        except Exception as e:
            duration = asyncio.get_event_loop().time() - start
            return ExecutionResult(
                success=False,
                error=f"Execution error: {e}",
                execution_time=duration,
            )

        finally:
            # Cleanup temp files — each removal is independent
            for p in [script_path, output_path, input_path]:
                try:
                    if p and os.path.exists(p):
                        os.remove(p)
                except Exception:
                    pass

    def _build_wrapper(
        self,
        code: str,
        has_input_data: bool,
        capture_figures: bool,
    ) -> str:
        """Build a wrapper script that captures output and figures.

        Args:
            code: User code to execute.
            has_input_data: Whether an input_data file was written.
            capture_figures: Whether to capture matplotlib figures.

        Returns:
            Complete wrapper script as a string.
        """
        # Indent the user code
        indented_code = textwrap.indent(code, "    ")

        figure_capture = ""
        if capture_figures:
            figure_capture = """
import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

# Patch savefig to capture figure paths
_original_savefig = plt.savefig
_figure_paths = []
_figure_counter = [0]

def _captured_savefig(*args, **kwargs):
    _figure_paths.append(args[0] if args else kwargs.get('fname', ''))
    return _original_savefig(*args, **kwargs)

plt.savefig = _captured_savefig
"""

        input_loading = ""
        if has_input_data:
            input_loading = """
import json
_input_path = os.environ.get('FUSION_SCIENCE_INPUT', '')
_input_data = {}
if _input_path and os.path.exists(_input_path):
    with open(_input_path, encoding='utf-8') as _f:
        _input_data = json.load(_f)
locals().update({'input_data': _input_data})
"""

        wrapper = f"""
import sys
import json
import os
import traceback

{figure_capture}
{input_loading}

try:
{indented_code}
    _success = True
    _error = ""
except Exception as _e:
    _success = False
    _error = "".join(traceback.format_exception(type(_e), _e, _e.__traceback__))

# Collect output
_output_data = {{
    "output": str(locals().get('result', '')),
    "figures": _figure_paths if '_figure_paths' in dir() else [],
    "success": _success,
    "error": _error,
}}

_output_path = os.environ.get('FUSION_SCIENCE_OUTPUT', '')
if _output_path:
    with open(_output_path, 'w', encoding='utf-8') as _f:
        json.dump(_output_data, _f, ensure_ascii=False)
"""

        return wrapper

    async def execute_r_code(self, code: str) -> ExecutionResult:
        """Execute R code using rpy2 (requires rpy2 extra).

        Args:
            code: R code to execute.

        Returns:
            ExecutionResult with R output.
        """
        start = asyncio.get_event_loop().time()
        try:
            import rpy2.robjects as robjects  # type: ignore[import-untyped]
            from rpy2.robjects import pandas2ri  # type: ignore[import-untyped]

            pandas2ri.activate()

            # Capture R output
            import io

            io.StringIO()
            io.StringIO()

            try:
                result = robjects.r(code)
                r_output = str(result)
                success = True
                error = ""
            except Exception as e:
                r_output = ""
                success = False
                error = str(e)

            duration = asyncio.get_event_loop().time() - start
            return ExecutionResult(
                success=success,
                stdout=r_output,
                error=error,
                execution_time=duration,
            )

        except ImportError:
            return ExecutionResult(
                success=False,
                error="rpy2 not installed. Install with: pip install fusion-science[r]",
                execution_time=asyncio.get_event_loop().time() - start,
            )

    @staticmethod
    def check_available_packages() -> list[dict[str, str]]:
        """Check which scientific Python packages are available.

        Returns:
            List of available packages with version info.
        """
        packages = [
            "numpy",
            "scipy",
            "pandas",
            "matplotlib",
            "seaborn",
            "sklearn",
            "biopython",
            "rdkit",
            "pymol",
            "py3Dmol",
            "statsmodels",
            "scikit-image",
            "plotly",
            "bokeh",
        ]
        available = []
        for pkg in packages:
            try:
                mod = __import__(pkg)
                version = getattr(mod, "__version__", "unknown")
                available.append({"name": pkg, "version": version, "available": True})
            except ImportError:
                available.append({"name": pkg, "available": False})
        return available
