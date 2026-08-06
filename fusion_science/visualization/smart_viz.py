from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from fusion_science.core.gateway import LLMGateway

logger = logging.getLogger(__name__)


@dataclass
class VizRecommendation:
    chart_type: str
    title: str
    description: str
    data_requirements: str
    suggested_config: dict = field(default_factory=dict)
    reasoning: str = ""
    confidence: float = 0.0


_KEYWORD_MAP: list[tuple[list[str], str, str]] = [
    (["expression", "differential", "volcano"], "volcano_plot", "Differential expression / volcano plot"),
    (["correlation", "scatter"], "scatter", "Scatter / correlation analysis"),
    (["time", "trend", "longitudinal"], "line_chart", "Time-series / trend line chart"),
    (["distribution", "compare", "groups"], "box_plot", "Distribution / group comparison box plot"),
    (["heatmap", "matrix", "gene"], "heatmap", "Heatmap / matrix clustering"),
    (["pathway", "enrichment", "go", "kegg"], "bar_chart", "Pathway enrichment bar chart"),
    (["composition", "proportion", "percentage"], "pie_chart", "Composition / proportion pie chart"),
    (["survival", "kaplan-meier"], "line_chart", "Survival / Kaplan-Meier curve"),
    (["pca", "dimensionality", "clustering"], "scatter", "PCA / dimensionality reduction scatter"),
]

_VIZ_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "recommendations": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "chart_type": {"type": "string"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "data_requirements": {"type": "string"},
                    "suggested_config": {"type": "object"},
                    "reasoning": {"type": "string"},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": ["chart_type", "title", "description", "data_requirements", "reasoning", "confidence"],
            },
            "minItems": 2,
            "maxItems": 3,
        }
    },
    "required": ["recommendations"],
}


class SmartVisualizer:
    def __init__(self, gateway: LLMGateway | None = None):
        self._gateway = gateway
        logger.debug("SmartVisualizer init, gateway=%s", type(gateway).__name__ if gateway else "None")

    async def recommend(
        self,
        data_description: str,
        analysis_goal: str = "",
    ) -> list[VizRecommendation]:
        logger.info("Recommend viz: desc=%r, goal=%r", data_description[:80], analysis_goal[:80])

        if self._gateway is not None:
            result = await self._recommend_via_llm(data_description, analysis_goal)
            if result:
                return result
            logger.warning("LLM recommendation failed, falling back to rule-based")

        return self._rule_based_recommend(data_description, analysis_goal)

    async def _recommend_via_llm(
        self,
        data_description: str,
        analysis_goal: str,
    ) -> list[VizRecommendation] | None:
        user_content = f"Data: {data_description}"
        if analysis_goal:
            user_content += f"\nGoal: {analysis_goal}"

        messages = [
            {
                "role": "system",
                "content": (
                    "You are a scientific visualization expert. "
                    "Recommend 2-3 chart types best suited for the described data and analysis goal. "
                    "Be specific about chart types used in bioinformatics, genomics, and scientific research."
                ),
            },
            {"role": "user", "content": user_content},
        ]

        try:
            llm_result = await self._gateway.structured_output(
                messages=messages,
                schema=_VIZ_SCHEMA,
                temperature=0.2,
            )
        except Exception as e:
            logger.error("LLM structured_output error: %s", e)
            return None

        if llm_result.error or not llm_result.parsed:
            logger.error("LLM result error: %s", llm_result.error)
            return None

        try:
            raw_list = llm_result.parsed.get("recommendations", [])
            recs: list[VizRecommendation] = []
            for item in raw_list:
                recs.append(
                    VizRecommendation(
                        chart_type=item.get("chart_type", "unknown"),
                        title=item.get("title", ""),
                        description=item.get("description", ""),
                        data_requirements=item.get("data_requirements", ""),
                        suggested_config=item.get("suggested_config", {}),
                        reasoning=item.get("reasoning", ""),
                        confidence=float(item.get("confidence", 0.5)),
                    )
                )
            logger.info("LLM returned %d recommendations", len(recs))
            return recs
        except Exception as e:
            logger.error("Failed to parse LLM recommendations: %s", e)
            return None

    def _rule_based_recommend(
        self,
        data_description: str,
        analysis_goal: str,
    ) -> list[VizRecommendation]:
        combined = f"{data_description} {analysis_goal}".lower()
        scores: dict[str, float] = {}

        for keywords, chart_type, _desc in _KEYWORD_MAP:
            for kw in keywords:
                if kw in combined:
                    scores[chart_type] = scores.get(chart_type, 0.0) + 1.0

        if not scores:
            scores = {
                "scatter": 0.4,
                "bar_chart": 0.3,
                "line_chart": 0.2,
            }

        sorted_charts = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:3]
        max_score = max(s for _, s in sorted_charts) if sorted_charts else 1.0

        recs: list[VizRecommendation] = []
        for chart_type, score in sorted_charts:
            confidence = round(min(score / max_score, 1.0) * 0.9 + 0.1, 2) if max_score > 0 else 0.3
            desc = self._chart_description(chart_type)
            recs.append(
                VizRecommendation(
                    chart_type=chart_type,
                    title=desc["title"],
                    description=desc["description"],
                    data_requirements=desc["data_requirements"],
                    suggested_config=desc.get("suggested_config", {}),
                    reasoning=f"Keyword match for '{chart_type}' with score {score:.1f}",
                    confidence=confidence,
                )
            )

        logger.info("Rule-based recommendations: %s", [r.chart_type for r in recs])
        return recs

    @staticmethod
    def _chart_description(chart_type: str) -> dict[str, Any]:
        _descriptions: dict[str, dict[str, Any]] = {
            "volcano_plot": {
                "title": "Volcano Plot",
                "description": "Shows log2 fold change vs -log10 p-value for differential expression",
                "data_requirements": "Gene/expression matrix with fold change and p-values",
                "suggested_config": {"x_label": "log2 Fold Change", "y_label": "-log10 p-value"},
            },
            "scatter": {
                "title": "Scatter Plot",
                "description": "Displays relationship between two continuous variables",
                "data_requirements": "Two numeric columns for x and y axes",
                "suggested_config": {"trendline": True, "alpha": 0.6},
            },
            "line_chart": {
                "title": "Line Chart",
                "description": "Shows trends over time or ordered categories",
                "data_requirements": "Time series or ordered sequence data",
                "suggested_config": {"marker": "o", "linewidth": 2},
            },
            "box_plot": {
                "title": "Box Plot",
                "description": "Compares distributions across groups",
                "data_requirements": "Numeric values with categorical grouping variable",
                "suggested_config": {"notch": False, "showfliers": True},
            },
            "heatmap": {
                "title": "Heatmap",
                "description": "Displays matrix values as color intensity",
                "data_requirements": "2D numeric matrix (e.g., gene expression)",
                "suggested_config": {"colormap": "viridis", "cluster_rows": True},
            },
            "bar_chart": {
                "title": "Bar Chart",
                "description": "Compares quantities across categories",
                "data_requirements": "Categorical labels with numeric values",
                "suggested_config": {"orientation": "vertical", "show_values": True},
            },
            "pie_chart": {
                "title": "Pie Chart",
                "description": "Shows composition as proportional slices",
                "data_requirements": "Categories with percentage or count data",
                "suggested_config": {"show_percentage": True},
            },
        }
        return _descriptions.get(
            chart_type,
            {
                "title": chart_type.replace("_", " ").title(),
                "description": f"Visualization type: {chart_type}",
                "data_requirements": "Numeric or categorical data",
                "suggested_config": {},
            },
        )
