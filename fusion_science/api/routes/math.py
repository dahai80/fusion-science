from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel

from ...literature.math_explainer import MathExplainer

logger = logging.getLogger(__name__)
router = APIRouter()


class ExplainFormulaRequest(BaseModel):
    formula: str


class ExplainTextRequest(BaseModel):
    text: str


@router.post("/explain")
async def explain_formula(request: Request, body: ExplainFormulaRequest):
    gateway = getattr(request.app.state, "gateway", None)
    explainer = MathExplainer(gateway=gateway)
    result = await explainer.explain_with_llm(body.formula)
    return result.to_dict()


@router.post("/explain-text")
async def explain_text(request: Request, body: ExplainTextRequest):
    explainer = MathExplainer()
    results = explainer.explain_text(body.text)
    return {"explanations": [r.to_dict() for r in results]}
