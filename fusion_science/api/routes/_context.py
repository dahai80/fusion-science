# api/routes/_context.py — session context summarization for sequential orchestration
# Issue #7: search->analyze->review 上下文衔接
# Importers: api/routes/analysis.py, api/routes/review.py
# API: build_context_prompt(session, step) -> enriched task string for agent.run()

from __future__ import annotations

import logging

from ...session.models import ResearchSession

logger = logging.getLogger(__name__)

_MAX_PAPERS_IN_PROMPT = 8
_MAX_PAPER_TITLE = 120
_MAX_ARTIFACT_SNIPPET = 300


def _summarize_papers(papers: list[dict]) -> str:
    if not papers:
        return ""
    lines = []
    for p in papers[:_MAX_PAPERS_IN_PROMPT]:
        title = (p.get("title") or "(untitled)")[:_MAX_PAPER_TITLE]
        year = p.get("year") or ""
        journal = p.get("journal") or ""
        meta = " ".join(s for s in (str(year), journal) if s)
        lines.append(f"  - {title}{f' ({meta})' if meta else ''}")
    more = len(papers) - _MAX_PAPERS_IN_PROMPT
    if more > 0:
        lines.append(f"  ...还有 {more} 篇")
    return "\n".join(lines)


def _summarize_artifacts(artifacts: list, exclude_types: tuple[str, ...] = ()) -> str:
    snippets = []
    for a in artifacts:
        if a.type in exclude_types:
            continue
        content = (a.content or "").strip()
        if not content:
            continue
        snippets.append(f"  - [{a.type}] {content[:_MAX_ARTIFACT_SNIPPET]}")
        if len(snippets) >= 4:
            break
    return "\n".join(snippets)


def build_context_prompt(session: ResearchSession, step: str, user_query: str) -> str:
    """Build an enriched task prompt that injects prior session context.

    Args:
        session: current ResearchSession (must already exist).
        step: orchestration step name ("analyze" or "review").
        user_query: the user's raw query for this step.

    Returns:
        Enriched task string to pass to agent.run().
    """
    sections: list[str] = []

    papers = getattr(session.context, "papers", []) or []
    if papers:
        sections.append(f"【前序检索结果 - 共 {len(papers)} 篇文献】\n{_summarize_papers(papers)}")

    if step == "review":
        analysis_snippets = _summarize_artifacts(session.artifacts, exclude_types=("search_result",))
        if analysis_snippets:
            sections.append(f"【前序分析产出】\n{analysis_snippets}")
    elif step == "analyze":
        search_snippets = _summarize_artifacts(session.artifacts, exclude_types=())
        if search_snippets:
            sections.append(f"【前序检索摘要】\n{search_snippets}")

    if not sections:
        logger.debug("build_context_prompt[%s]: no prior context, using raw query", step)
        return user_query

    context_block = "\n\n".join(sections)
    prompt = (
        f"{context_block}\n\n"
        f"---\n请基于以上前序步骤的上下文，处理当前请求（保持与上文衔接，可引用前序结果）：\n{user_query}"
    )
    logger.info("build_context_prompt[%s]: enriched with %d context section(s)", step, len(sections))
    return prompt
