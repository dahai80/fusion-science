"""R language executor — run R code for statistical analysis and bioinformatics.

Provides a bridge to the R runtime via rpy2 for executing R code
within the fusion-science workflow. Supports base R, Bioconductor,
and common R packages for bioinformatics.
"""

from __future__ import annotations

import logging
import os
import tempfile
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class RExecutionResult:
    """Result of R code execution."""

    success: bool
    output: str = ""
    error: str = ""
    plots: list[str] = field(default_factory=list)
    execution_time: float = 0.0
    dataframes: dict[str, int] = field(default_factory=dict)  # name -> rows


class RExecutor:
    """Execute R code for statistical analysis and bioinformatics.

    Requires rpy2 package. Supports:
    - Base R statistics and plotting
    - Bioconductor packages (DESeq2, limma, etc.)
    - Common R data science packages (tidyverse, ggplot2, etc.)
    """

    def __init__(self, timeout: int = 120):
        self.timeout = timeout
        self._r_available = False
        self._check_r()

    def _check_r(self) -> None:
        """Check if R and rpy2 are available."""
        try:
            import rpy2.robjects as robjects  # type: ignore[import-untyped]
            robjects.r("version")
            self._r_available = True
        except ImportError:
            self._r_available = False
        except Exception as e:
            logger.warning("R check failed: %s", e)
            self._r_available = False

    @property
    def available(self) -> bool:
        """Check if R is available."""
        return self._r_available

    async def execute(self, code: str, capture_plots: bool = True) -> RExecutionResult:
        """Execute R code.

        Args:
            code: R code to execute.
            capture_plots: Whether to capture R plots.

        Returns:
            RExecutionResult with output and plots.
        """
        start = time.time()

        if not self._r_available:
            return RExecutionResult(
                success=False,
                error="R is not available. Install R and rpy2 (pip install fusion-science[r])",
            )

        try:
            import rpy2.robjects as robjects  # type: ignore[import-untyped]
            from rpy2.robjects import pandas2ri  # type: ignore[import-untyped]

            pandas2ri.activate()

            # Capture plots if requested
            if capture_plots:
                plot_paths = self._setup_plot_capture()
                code = self._wrap_with_plot_capture(code, plot_paths)

            # Execute R code
            output = []
            error = []

            try:
                result = robjects.r(code)
                if result is not None:
                    output.append(str(result))
            except Exception as e:
                error.append(str(e))

            duration = time.time() - start

            # Collect plots
            plots = []
            if capture_plots:
                plots = self._collect_plots()

            return RExecutionResult(
                success=not bool(error),
                output="\n".join(output),
                error="\n".join(error),
                plots=plots,
                execution_time=duration,
            )

        except Exception as e:
            duration = time.time() - start
            return RExecutionResult(
                success=False,
                error=str(e),
                execution_time=duration,
            )

    def _setup_plot_capture(self) -> list[str]:
        """Set up R plot capture to temporary files.

        Returns:
            List of temporary file paths for plots.
        """
        import tempfile
        tempfile.mkdtemp(prefix="fusion_r_plots_")
        plot_paths = []
        return plot_paths

    def _wrap_with_plot_capture(self, code: str, plot_paths: list[str]) -> str:
        """Wrap R code with plot capture commands.

        Args:
            code: Original R code.
            plot_paths: Paths to save plots.

        Returns:
            Modified R code with plot capture.
        """
        # Redirect R plots to PNG files
        tempfile.gettempdir()
        wrapped = f"""
# Fusion-Science: plot capture enabled
options(device = function() png(tempfile(fileext = ".png"), width = 800, height = 600, res = 120))
{code}
"""
        return wrapped

    def _collect_plots(self) -> list[str]:
        """Collect captured plot files.

        Returns:
            List of plot file paths.
        """
        import glob
        import tempfile

        plot_dir = tempfile.gettempdir()
        plots = glob.glob(os.path.join(plot_dir, "fusion_r_plots_*.png"))
        # Rename to more descriptive names
        named_plots = []
        for i, plot in enumerate(plots):
            new_name = os.path.join(plot_dir, f"fusion_r_plot_{i}.png")
            try:
                os.rename(plot, new_name)
                named_plots.append(new_name)
            except Exception:
                named_plots.append(plot)
        return named_plots

    async def check_packages(self, packages: list[str]) -> dict[str, bool]:
        """Check if R packages are installed.

        Args:
            packages: List of R package names.

        Returns:
            Dict mapping package name to availability.
        """
        if not self._r_available:
            return {pkg: False for pkg in packages}

        try:
            import rpy2.robjects as robjects  # type: ignore[import-untyped]
            result = {}
            for pkg in packages:
                try:
                    robjects.r(f"library({pkg})")
                    result[pkg] = True
                except Exception:
                    result[pkg] = False
            return result
        except Exception:
            return {pkg: False for pkg in packages}

    @staticmethod
    def get_bioconductor_install_code() -> str:
        """Get R code for installing Bioconductor packages.

        Returns:
            R code string for Bioconductor installation.
        """
        return """
if (!requireNamespace("BiocManager", quietly = TRUE))
    install.packages("BiocManager")
BiocManager::install(version = "3.18")
"""
