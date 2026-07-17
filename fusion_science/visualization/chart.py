"""Scientific chart generation — publication-quality statistical plots.

Provides a unified interface for generating scientific charts using
matplotlib and seaborn, with support for common plot types used in
life sciences and bioinformatics publications.
"""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ChartConfig:
    """Configuration for chart generation."""

    width: int = 8
    height: int = 6
    dpi: int = 300
    font_size: int = 12
    font_family: str = "sans-serif"
    style: str = "whitegrid"  # seaborn style
    palette: str = "Set2"  # Color palette
    title: str = ""
    xlabel: str = ""
    ylabel: str = ""
    output_format: str = "png"  # png, svg, pdf


@dataclass
class ChartResult:
    """Result of chart generation."""

    success: bool
    file_path: str = ""
    error: str = ""
    mime_type: str = "image/png"


class ChartGenerator:
    """Generates publication-quality scientific charts.

    Supports common bioinformatics and life science visualization types.
    """

    def __init__(self, config: ChartConfig | None = None):
        self.config = config or ChartConfig()
        self._figure_counter = 0

    def _setup_style(self) -> None:
        """Apply matplotlib/seaborn style settings."""
        try:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            import seaborn as sns

            sns.set_style(self.config.style)
            sns.set_palette(self.config.palette)
            plt.rcParams.update({
                "figure.dpi": self.config.dpi,
                "font.size": self.config.font_size,
                "font.family": self.config.font_family,
                "figure.figsize": (self.config.width, self.config.height),
            })
        except ImportError:
            logger.warning("matplotlib/seaborn not available for styling")

    def _get_output_path(self, name: str) -> str:
        """Get an output file path."""
        output_dir = tempfile.gettempdir()
        self._figure_counter += 1
        return os.path.join(
            output_dir,
            f"fusion_chart_{name}_{self._figure_counter}.{self.config.output_format}",
        )

    async def bar_chart(
        self,
        categories: list[str],
        values: list[float],
        errors: list[float] | None = None,
        config: ChartConfig | None = None,
        output_path: str | None = None,
    ) -> ChartResult:
        """Generate a bar chart (e.g., for gene expression comparison).

        Args:
            categories: Category labels.
            values: Values for each category.
            errors: Optional error bars.
            config: Chart configuration overrides.
            output_path: Output file path.

        Returns:
            ChartResult with file path.
        """
        cfg = config or self.config
        self._setup_style()
        output_path = output_path or self._get_output_path("bar")

        try:
            import matplotlib.pyplot as plt
            import numpy as np

            fig, ax = plt.subplots(figsize=(cfg.width, cfg.height))
            x = np.arange(len(categories))
            bars = ax.bar(x, values, yerr=errors, capsize=5, alpha=0.8)

            ax.set_xticks(x)
            ax.set_xticklabels(categories, rotation=45, ha="right")
            if cfg.title:
                ax.set_title(cfg.title)
            if cfg.xlabel:
                ax.set_xlabel(cfg.xlabel)
            if cfg.ylabel:
                ax.set_ylabel(cfg.ylabel)

            plt.tight_layout()
            plt.savefig(output_path, dpi=cfg.dpi, bbox_inches="tight")
            plt.close()

            return ChartResult(success=True, file_path=output_path)

        except Exception as e:
            logger.error("Bar chart generation failed: %s", e)
            return ChartResult(success=False, error=str(e))

    async def scatter_plot(
        self,
        x: list[float],
        y: list[float],
        groups: list[str] | None = None,
        config: ChartConfig | None = None,
        output_path: str | None = None,
    ) -> ChartResult:
        """Generate a scatter plot (e.g., for correlation analysis).

        Args:
            x: X-axis values.
            y: Y-axis values.
            groups: Optional group labels for coloring.
            config: Chart configuration overrides.
            output_path: Output file path.

        Returns:
            ChartResult with file path.
        """
        cfg = config or self.config
        self._setup_style()
        output_path = output_path or self._get_output_path("scatter")

        try:
            import matplotlib.pyplot as plt
            import seaborn as sns

            fig, ax = plt.subplots(figsize=(cfg.width, cfg.height))

            if groups:
                import pandas as pd
                data = pd.DataFrame({"x": x, "y": y, "group": groups})
                sns.scatterplot(data=data, x="x", y="y", hue="group", ax=ax)
            else:
                ax.scatter(x, y, alpha=0.7)

            if cfg.title:
                ax.set_title(cfg.title)
            if cfg.xlabel:
                ax.set_xlabel(cfg.xlabel)
            if cfg.ylabel:
                ax.set_ylabel(cfg.ylabel)

            plt.tight_layout()
            plt.savefig(output_path, dpi=cfg.dpi, bbox_inches="tight")
            plt.close()

            return ChartResult(success=True, file_path=output_path)

        except Exception as e:
            logger.error("Scatter plot generation failed: %s", e)
            return ChartResult(success=False, error=str(e))

    async def heatmap(
        self,
        data: list[list[float]],
        row_labels: list[str] | None = None,
        col_labels: list[str] | None = None,
        config: ChartConfig | None = None,
        output_path: str | None = None,
    ) -> ChartResult:
        """Generate a heatmap (e.g., for gene expression data).

        Args:
            data: 2D matrix of values.
            row_labels: Labels for rows.
            col_labels: Labels for columns.
            config: Chart configuration overrides.
            output_path: Output file path.

        Returns:
            ChartResult with file path.
        """
        cfg = config or self.config
        self._setup_style()
        output_path = output_path or self._get_output_path("heatmap")

        try:
            import matplotlib.pyplot as plt
            import seaborn as sns
            import numpy as np

            fig, ax = plt.subplots(figsize=(cfg.width, cfg.height))
            data_arr = np.array(data)

            sns.heatmap(
                data_arr,
                xticklabels=col_labels,
                yticklabels=row_labels,
                cmap="viridis",
                annot=False,
                ax=ax,
            )

            if cfg.title:
                ax.set_title(cfg.title)
            if cfg.xlabel:
                ax.set_xlabel(cfg.xlabel)
            if cfg.ylabel:
                ax.set_ylabel(cfg.ylabel)

            plt.tight_layout()
            plt.savefig(output_path, dpi=cfg.dpi, bbox_inches="tight")
            plt.close()

            return ChartResult(success=True, file_path=output_path)

        except Exception as e:
            logger.error("Heatmap generation failed: %s", e)
            return ChartResult(success=False, error=str(e))

    async def volcano_plot(
        self,
        log2fc: list[float],
        pvalues: list[float],
        labels: list[str] | None = None,
        fc_threshold: float = 1.0,
        p_threshold: float = 0.05,
        config: ChartConfig | None = None,
        output_path: str | None = None,
    ) -> ChartResult:
        """Generate a volcano plot (e.g., for differential expression analysis).

        Args:
            log2fc: Log2 fold change values.
            pvalues: P-values.
            labels: Optional gene/probe labels.
            fc_threshold: Fold change significance threshold.
            p_threshold: P-value significance threshold.
            config: Chart configuration overrides.
            output_path: Output file path.

        Returns:
            ChartResult with file path.
        """
        cfg = config or self.config
        self._setup_style()
        output_path = output_path or self._get_output_path("volcano")

        try:
            import matplotlib.pyplot as plt
            import numpy as np

            fig, ax = plt.subplots(figsize=(cfg.width, cfg.height))
            neg_log_p = [-np.log10(p) if p > 0 else 10 for p in pvalues]

            # Classify points
            up = [i for i in range(len(log2fc)) if log2fc[i] > fc_threshold and neg_log_p[i] > -np.log10(p_threshold)]
            down = [i for i in range(len(log2fc)) if log2fc[i] < -fc_threshold and neg_log_p[i] > -np.log10(p_threshold)]
            ns = [i for i in range(len(log2fc)) if i not in up and i not in down]

            ax.scatter([log2fc[i] for i in ns], [neg_log_p[i] for i in ns], alpha=0.5, s=10, label="NS")
            ax.scatter([log2fc[i] for i in up], [neg_log_p[i] for i in up], alpha=0.7, s=15, color="red", label="Up")
            ax.scatter([log2fc[i] for i in down], [neg_log_p[i] for i in down], alpha=0.7, s=15, color="blue", label="Down")

            # Threshold lines
            ax.axhline(-np.log10(p_threshold), color="grey", linestyle="--", alpha=0.5)
            ax.axvline(fc_threshold, color="grey", linestyle="--", alpha=0.5)
            ax.axvline(-fc_threshold, color="grey", linestyle="--", alpha=0.5)

            if labels:
                for i in up + down:
                    if i < len(labels):
                        ax.annotate(labels[i], (log2fc[i], neg_log_p[i]), fontsize=8, alpha=0.8)

            ax.set_xlabel("Log2 Fold Change")
            ax.set_ylabel("-Log10 P-value")
            if cfg.title:
                ax.set_title(cfg.title)
            ax.legend()

            plt.tight_layout()
            plt.savefig(output_path, dpi=cfg.dpi, bbox_inches="tight")
            plt.close()

            return ChartResult(success=True, file_path=output_path)

        except Exception as e:
            logger.error("Volcano plot generation failed: %s", e)
            return ChartResult(success=False, error=str(e))

    async def line_chart(
        self,
        x: list[float],
        y_sets: list[dict[str, Any]],
        config: ChartConfig | None = None,
        output_path: str | None = None,
    ) -> ChartResult:
        """Generate a line chart (e.g., for time series or growth curves).

        Args:
            x: X-axis values.
            y_sets: List of dicts with 'label' and 'values' keys.
            config: Chart configuration overrides.
            output_path: Output file path.

        Returns:
            ChartResult with file path.
        """
        cfg = config or self.config
        self._setup_style()
        output_path = output_path or self._get_output_path("line")

        try:
            import matplotlib.pyplot as plt
            import seaborn as sns

            fig, ax = plt.subplots(figsize=(cfg.width, cfg.height))

            for y_set in y_sets:
                ax.plot(x, y_set["values"], label=y_set.get("label", ""), marker="o", linewidth=2)

            if cfg.title:
                ax.set_title(cfg.title)
            if cfg.xlabel:
                ax.set_xlabel(cfg.xlabel)
            if cfg.ylabel:
                ax.set_ylabel(cfg.ylabel)
            ax.legend()
            ax.grid(True, alpha=0.3)

            plt.tight_layout()
            plt.savefig(output_path, dpi=cfg.dpi, bbox_inches="tight")
            plt.close()

            return ChartResult(success=True, file_path=output_path)

        except Exception as e:
            logger.error("Line chart generation failed: %s", e)
            return ChartResult(success=False, error=str(e))

    async def box_plot(
        self,
        data: dict[str, list[float]],
        config: ChartConfig | None = None,
        output_path: str | None = None,
    ) -> ChartResult:
        """Generate a box plot (e.g., for comparing distributions).

        Args:
            data: Dict mapping group names to value lists.
            config: Chart configuration overrides.
            output_path: Output file path.

        Returns:
            ChartResult with file path.
        """
        cfg = config or self.config
        self._setup_style()
        output_path = output_path or self._get_output_path("box")

        try:
            import matplotlib.pyplot as plt
            import seaborn as sns
            import pandas as pd

            # Convert to long format
            long_data = []
            for group, values in data.items():
                for v in values:
                    long_data.append({"group": group, "value": v})

            df = pd.DataFrame(long_data)
            fig, ax = plt.subplots(figsize=(cfg.width, cfg.height))
            sns.boxplot(data=df, x="group", y="value", ax=ax)

            if cfg.title:
                ax.set_title(cfg.title)
            if cfg.xlabel:
                ax.set_xlabel(cfg.xlabel)
            if cfg.ylabel:
                ax.set_ylabel(cfg.ylabel)

            plt.tight_layout()
            plt.savefig(output_path, dpi=cfg.dpi, bbox_inches="tight")
            plt.close()

            return ChartResult(success=True, file_path=output_path)

        except Exception as e:
            logger.error("Box plot generation failed: %s", e)
            return ChartResult(success=False, error=str(e))