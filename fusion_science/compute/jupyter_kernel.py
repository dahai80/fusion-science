"""Jupyter kernel integration — execute code in Jupyter kernels for interactive analysis.

Provides an interface to launch and communicate with Jupyter kernels,
supporting Python, R, Julia, and other Jupyter-compatible runtimes.
Enables interactive notebook-style execution within the science workflow.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class KernelResult:
    """Result of a single code execution in a kernel."""

    success: bool
    output: str = ""
    error: str = ""
    execution_count: int = 0
    mime_data: dict[str, str] = field(default_factory=dict)  # Rich display data


@dataclass
class KernelInfo:
    """Information about an available kernel."""

    name: str
    language: str
    display_name: str
    description: str = ""


class JupyterKernelManager:
    """Manages Jupyter kernels for interactive code execution.

    Supports multiple kernel types (Python, R, Julia) and manages
    kernel lifecycle (start, execute, shutdown).
    """

    def __init__(self, kernel_name: str = "python3"):
        self.kernel_name = kernel_name
        self._kernel_manager: Any = None
        self._kernel_client: Any = None
        self._connection_file: str = ""
        self._running = False

    async def start_kernel(self, kernel_name: str | None = None) -> bool:
        """Start a Jupyter kernel.

        Args:
            kernel_name: Optional kernel name override (e.g., "python3", "ir", "julia").

        Returns:
            True if the kernel started successfully.
        """
        try:
            from jupyter_client import KernelManager  # type: ignore[import-untyped]

            name = kernel_name or self.kernel_name
            self._kernel_manager = KernelManager(kernel_name=name)
            self._kernel_manager.start_kernel()

            # Wait for kernel to be ready
            self._kernel_client = self._kernel_manager.client()
            self._kernel_client.start_channels()

            # Wait for kernel info
            import time
            time.sleep(1)

            self._running = True
            logger.info("Jupyter kernel '%s' started successfully", name)
            return True

        except ImportError:
            logger.error(
                "jupyter-client not installed. Install with: pip install fusion-science[jupyter]"
            )
            return False
        except Exception as e:
            logger.error("Failed to start Jupyter kernel '%s': %s", kernel_name, e)
            return False

    async def execute(self, code: str, timeout: int = 120) -> KernelResult:
        """Execute code in the running kernel.

        Args:
            code: Code to execute.
            timeout: Execution timeout in seconds.

        Returns:
            KernelResult with output and rich display data.
        """
        if not self._running or self._kernel_client is None:
            return KernelResult(
                success=False,
                error="Kernel not running. Call start_kernel() first.",
            )

        try:
            # Execute code
            msg_id = self._kernel_client.execute(code)

            # Collect results
            output_parts = []
            error_parts = []
            mime_data = {}
            execution_count = 0

            while True:
                try:
                    msg = await asyncio.wait_for(
                        asyncio.get_event_loop().run_in_executor(
                            None, self._kernel_client.get_iopub_msg
                        ),
                        timeout=timeout,
                    )
                except asyncio.TimeoutError:
                    break

                msg_type = msg.get("msg_type", "")
                content = msg.get("content", {})

                if msg_type == "execute_result":
                    execution_count = content.get("execution_count", 0)
                    text = content.get("data", {}).get("text/plain", "")
                    if text:
                        output_parts.append(text)
                    # Capture rich display data
                    data = content.get("data", {})
                    for mime in ("text/html", "image/png", "image/svg+xml", "application/json"):
                        if mime in data:
                            mime_data[mime] = data[mime]

                elif msg_type == "stream":
                    text = content.get("text", "")
                    stream_name = content.get("name", "stdout")
                    output_parts.append(text)

                elif msg_type == "display_data":
                    data = content.get("data", {})
                    for mime in ("text/html", "image/png", "image/svg+xml", "application/json", "text/plain"):
                        if mime in data:
                            mime_data[mime] = data[mime]

                elif msg_type == "error":
                    traceback_lines = content.get("traceback", [])
                    error_parts.append("\n".join(traceback_lines))
                    break

                elif msg_type == "status":
                    status = content.get("execution_state", "")
                    if status == "idle":
                        break

            output = "\n".join(output_parts).strip()
            error = "\n".join(error_parts).strip()

            return KernelResult(
                success=not bool(error),
                output=output,
                error=error,
                execution_count=execution_count,
                mime_data=mime_data,
            )

        except Exception as e:
            logger.error("Kernel execution error: %s", e)
            return KernelResult(success=False, error=str(e))

    async def shutdown(self) -> None:
        """Shutdown the kernel and clean up resources.

        Always attempts both stop_channels and shutdown_kernel,
        even if one of them fails.
        """
        exc = None
        if self._kernel_client:
            try:
                self._kernel_client.stop_channels()
            except Exception as e:
                exc = e
                logger.warning("Failed to stop kernel channels: %s", e)
            self._kernel_client = None

        if self._kernel_manager:
            try:
                self._kernel_manager.shutdown_kernel()
            except Exception as e:
                exc = e
                logger.warning("Failed to shut down kernel manager: %s", e)
            self._kernel_manager = None

        self._running = False
        logger.info("Jupyter kernel shut down")
        if exc:
            raise exc

    @staticmethod
    def list_available_kernels() -> list[KernelInfo]:
        """List all available Jupyter kernels on the system.

        Returns:
            List of KernelInfo with available kernels.
        """
        try:
            import jupyter_client  # type: ignore[import-untyped]
            from jupyter_client.kernelspec import KernelSpecManager  # type: ignore[import-untyped]

            ksm = KernelSpecManager()
            kernels = ksm.get_all_specs()
            return [
                KernelInfo(
                    name=name,
                    language=spec.get("spec", {}).get("language", "unknown"),
                    display_name=spec.get("spec", {}).get("display_name", name),
                    description=spec.get("spec", {}).get("argv", [""])[0] if spec.get("spec", {}).get("argv") else "",
                )
                for name, spec in kernels.items()
            ]
        except ImportError:
            return []
        except Exception as e:
            logger.warning("Failed to list kernels: %s", e)
            return []

    @staticmethod
    def install_kernel(display_name: str = "Fusion Science") -> bool:
        """Install the fusion-science Python kernel spec.

        Args:
            display_name: Display name for the kernel.

        Returns:
            True if installed successfully.
        """
        try:
            import json
            import os
            from pathlib import Path
            import sys

            kernel_dir = Path.home() / ".local" / "share" / "jupyter" / "kernels" / "fusion-science"
            kernel_dir.mkdir(parents=True, exist_ok=True)

            kernel_json = {
                "argv": [
                    sys.executable,
                    "-m",
                    "ipykernel_launcher",
                    "-f",
                    "{connection_file}",
                ],
                "display_name": display_name,
                "language": "python",
                "env": {
                    "FUSION_SCIENCE_ENV": "1",
                },
            }

            with open(kernel_dir / "kernel.json", "w", encoding="utf-8") as f:
                json.dump(kernel_json, f, indent=2)

            logger.info("Fusion Science kernel installed at %s", kernel_dir)
            return True

        except Exception as e:
            logger.error("Failed to install kernel: %s", e)
            return False