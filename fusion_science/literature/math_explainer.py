from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

LATEX_SYMBOLS = {
    "\\alpha": "α", "\\beta": "β", "\\gamma": "γ", "\\delta": "δ",
    "\\epsilon": "ε", "\\theta": "θ", "\\lambda": "λ", "\\mu": "μ",
    "\\sigma": "σ", "\\omega": "ω", "\\phi": "φ", "\\psi": "ψ",
    "\\pi": "π", "\\rho": "ρ", "\\tau": "τ", "\\chi": "χ",
    "\\eta": "η", "\\kappa": "κ", "\\nu": "ν", "\\xi": "ξ",
}

FORMULA_PATTERNS = {
    r"p\s*[<>=]\s*0\.\d+": {
        "name": "p-value",
        "explanation": "Statistical significance test result. p < 0.05 indicates statistical significance.",
    },
    r"r\s*=\s*-?\d+\.?\d*": {
        "name": "correlation coefficient",
        "explanation": "Pearson correlation coefficient. |r| > 0.7 is strong, 0.3-0.7 moderate, < 0.3 weak.",
    },
    r"OR\s*=\s*\d+\.?\d*": {
        "name": "odds ratio",
        "explanation": "Odds ratio. OR > 1 indicates increased risk; OR < 1 indicates decreased risk.",
    },
    r"HR\s*=\s*\d+\.?\d*": {
        "name": "hazard ratio",
        "explanation": "Hazard ratio from survival analysis. HR > 1 indicates higher hazard.",
    },
    r"CI\s*:?\s*[\[(].*?[\])]": {
        "name": "confidence interval",
        "explanation": "Confidence interval. 95% CI is standard; if CI does not cross 1 (for OR/HR), result is significant.",
    },
    r"n\s*=\s*\d+": {
        "name": "sample size",
        "explanation": "Number of observations or participants in the study.",
    },
    r"F\s*\(\d+,\s*\d+\)\s*=\s*\d+\.?\d*": {
        "name": "F-statistic",
        "explanation": "F-test statistic from ANOVA. Tests whether group means are significantly different.",
    },
    r"t\s*\(\d+\)\s*=\s*-?\d+\.?\d*": {
        "name": "t-statistic",
        "explanation": "Student's t-test statistic. Tests difference between two group means.",
    },
    r"\\chi\^?2\s*=\s*\d+\.?\d*": {
        "name": "chi-square statistic",
        "explanation": "Chi-square test statistic for categorical data independence testing.",
    },
    r"AUC\s*=\s*\d+\.?\d*": {
        "name": "AUC (Area Under Curve)",
        "explanation": "Area Under the ROC Curve. AUC = 1 is perfect; 0.5 is random; > 0.8 is good.",
    },
    r"I\^?2\s*=\s*\d+\.?\d*%?": {
        "name": "I-squared heterogeneity",
        "explanation": "Heterogeneity statistic in meta-analysis. I² > 50% indicates substantial heterogeneity.",
    },
    r"d\s*=\s*-?\d+\.?\d*": {
        "name": "Cohen's d",
        "explanation": "Effect size measure. d = 0.2 small, 0.5 medium, 0.8 large effect.",
    },
}


@dataclass
class FormulaExplanation:
    original: str
    name: str = ""
    explanation: str = ""
    symbols: list[str] = field(default_factory=list)
    plain_text: str = ""

    def to_dict(self) -> dict:
        return {
            "original": self.original,
            "name": self.name,
            "explanation": self.explanation,
            "symbols": self.symbols,
            "plain_text": self.plain_text,
        }


class MathExplainer:
    def __init__(self, gateway=None):
        self.gateway = gateway

    def explain(self, formula: str) -> FormulaExplanation:
        result = FormulaExplanation(original=formula)
        result.symbols = self._extract_symbols(formula)
        result.plain_text = self._latex_to_plain(formula)

        for pattern, info in FORMULA_PATTERNS.items():
            if re.search(pattern, formula):
                result.name = info["name"]
                result.explanation = info["explanation"]
                break

        if not result.name:
            result.name = "mathematical expression"
            result.explanation = self._generic_explanation(formula)

        logger.debug("Explained formula: %s -> %s", formula[:50], result.name)
        return result

    def explain_text(self, text: str) -> list[FormulaExplanation]:
        results = []
        inline = re.findall(r"\$([^$]+)\$", text)
        display = re.findall(r"\$\$(.+?)\$\$", text, re.DOTALL)
        for f in display:
            results.append(self.explain(f.strip()))
        for f in inline:
            if f.strip() not in {r.original for r in results}:
                results.append(self.explain(f.strip()))

        for pattern, info in FORMULA_PATTERNS.items():
            matches = re.findall(pattern, text)
            for m in matches:
                already = any(r.original == m for r in results)
                if not already:
                    exp = FormulaExplanation(
                        original=m,
                        name=info["name"],
                        explanation=info["explanation"],
                    )
                    results.append(exp)

        logger.info("Explained %d formulas in text", len(results))
        return results

    async def explain_with_llm(self, formula: str) -> FormulaExplanation:
        rule_result = self.explain(formula)

        if not self.gateway:
            return rule_result

        try:
            messages = [
                {
                    "role": "system",
                    "content": (
                        "You are a scientific math explainer. Explain the following formula "
                        "in simple terms. Return JSON with keys: name, explanation, plain_text. "
                        "Keep explanation under 100 words."
                    ),
                },
                {"role": "user", "content": formula},
            ]
            result = await self.gateway.structured_output(
                messages,
                schema={
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "explanation": {"type": "string"},
                        "plain_text": {"type": "string"},
                    },
                    "required": ["name", "explanation", "plain_text"],
                },
                model=self.gateway.get_model_for_role("reasoning"),
            )
            if result.parsed and not result.error:
                rule_result.name = result.parsed.get("name", rule_result.name)
                rule_result.explanation = result.parsed.get("explanation", rule_result.explanation)
                rule_result.plain_text = result.parsed.get("plain_text", rule_result.plain_text)
        except Exception as e:
            logger.warning("LLM math explanation failed: %s", e)

        return rule_result

    def _extract_symbols(self, formula: str) -> list[str]:
        symbols = []
        for latex, plain in LATEX_SYMBOLS.items():
            if latex in formula:
                symbols.append(f"{latex} = {plain}")
        return symbols

    def _latex_to_plain(self, formula: str) -> str:
        text = formula
        for latex, plain in LATEX_SYMBOLS.items():
            text = text.replace(latex, plain)
        text = re.sub(r"\^{(\w+)}", r"^\1", text)
        text = re.sub(r"_{(\w+)}", r"_\1", text)
        text = re.sub(r"[{}\\]", "", text)
        return text.strip()

    def _generic_explanation(self, formula: str) -> str:
        if "=" in formula:
            return "An equation defining a relationship between variables."
        if any(op in formula for op in ["+", "-", "*", "/", "^"]):
            return "A mathematical expression involving arithmetic operations."
        return "A mathematical or statistical expression."
