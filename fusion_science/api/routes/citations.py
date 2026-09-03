from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from pydantic import BaseModel

from ...literature.citation import CitationManager
from ...literature.search import Paper
from .._owner import check_owner

logger = logging.getLogger(__name__)
router = APIRouter()


class AddCitationRequest(BaseModel):
    title: str
    authors: list[str] = []
    year: str = ""
    journal: str = ""
    doi: str = ""
    pmid: str = ""
    keywords: list[str] = []


@router.get("/graph")
async def get_citation_graph(request: Request, session_id: str):
    mgr = CitationManager()
    session = request.app.state.session_manager.get_session(session_id)
    if session:
        # P1 (S5): IDOR guard — only when the session exists; a missing
        # session_id yields an empty graph (200), not a 404, matching the
        # route's optional-session contract.
        denied = check_owner(request, session)
        if denied is not None:
            return denied
        for artifact in session.artifacts:
            if artifact.type == "citation":
                try:
                    p = Paper(
                        title=artifact.data.get("title", ""),
                        authors=artifact.data.get("authors", []),
                        year=artifact.data.get("year", ""),
                        journal=artifact.data.get("journal", ""),
                        doi=artifact.data.get("doi", ""),
                        pmid=artifact.data.get("pmid", ""),
                        keywords=artifact.data.get("keywords", []),
                    )
                    mgr.add_paper(p)
                except Exception as e:
                    logger.warning("Failed to add citation artifact: %s", e)

    graph = mgr.build_graph()
    return graph.to_dict()


@router.post("/add")
async def add_citation(request: Request, body: AddCitationRequest):
    mgr = CitationManager()
    paper = Paper(
        title=body.title,
        authors=body.authors,
        year=body.year,
        journal=body.journal,
        doi=body.doi,
        pmid=body.pmid,
        keywords=body.keywords,
    )
    citation = mgr.add_paper(paper)
    return {"key": citation.key, "formatted": citation.style_cache}


@router.get("/bibliography")
async def get_bibliography(request: Request, style: str = "apa"):
    mgr = CitationManager()
    session_id = request.query_params.get("session_id", "")
    if session_id:
        session = request.app.state.session_manager.get_session(session_id)
        if session:
            # P1 (S5): IDOR guard — same rationale as /graph.
            denied = check_owner(request, session)
            if denied is not None:
                return denied
            for artifact in session.artifacts:
                if artifact.type == "citation":
                    try:
                        p = Paper(
                            title=artifact.data.get("title", ""),
                            authors=artifact.data.get("authors", []),
                            year=artifact.data.get("year", ""),
                            journal=artifact.data.get("journal", ""),
                            doi=artifact.data.get("doi", ""),
                            pmid=artifact.data.get("pmid", ""),
                        )
                        mgr.add_paper(p)
                    except Exception:
                        pass

    bib = mgr.generate_bibliography(style=style)
    return {"style": style, "bibliography": bib}
