"""Citation manager — citation management with multiple styles.

Provides BibTeX/APA/Vancouver formatting, citation deduplication,
citation graph construction, and verification.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from .search import Paper

logger = logging.getLogger(__name__)


@dataclass
class Citation:
    key: str
    paper: Paper
    style_cache: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "key": self.key,
            "title": self.paper.title,
            "authors": self.paper.authors,
            "year": self.paper.year,
            "doi": self.paper.doi,
            "formatted": self.style_cache,
        }


@dataclass
class CitationGraph:
    nodes: dict[str, Citation] = field(default_factory=dict)
    edges: list[tuple[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "node_count": len(self.nodes),
            "edge_count": len(self.edges),
            "nodes": {k: v.to_dict() for k, v in self.nodes.items()},
            "edges": self.edges,
        }


class CitationManager:
    def __init__(self):
        self._citations: dict[str, Citation] = {}
        self._key_counter: int = 0

    def add_paper(self, paper: Paper, key: str = "") -> Citation:
        key = key or self._generate_key(paper)
        if key in self._citations:
            existing = self._citations[key]
            if existing.paper.title == paper.title:
                return existing
            key = f"{key}_{self._key_counter}"
            self._key_counter += 1

        citation = Citation(key=key, paper=paper)
        citation.style_cache = {
            "apa": self.format_apa(paper),
            "vancouver": self.format_vancouver(paper, len(self._citations) + 1),
            "bibtex": self.format_bibtex(paper, key),
        }
        self._citations[key] = citation
        logger.debug("Added citation: %s", key)
        return citation

    def add_papers(self, papers: list[Paper]) -> list[Citation]:
        return [self.add_paper(p) for p in papers]

    def get_citation(self, key: str) -> Citation | None:
        return self._citations.get(key)

    def get_all_citations(self) -> list[Citation]:
        return list(self._citations.values())

    def remove_citation(self, key: str) -> bool:
        if key in self._citations:
            del self._citations[key]
            logger.debug("Removed citation: %s", key)
            return True
        return False

    def format_apa(self, paper: Paper) -> str:
        authors = self._format_authors_apa(paper.authors)
        parts = [f"{authors} ({paper.year})."]
        parts.append(paper.title)
        if paper.journal:
            parts.append(f"*{paper.journal}*")
        if paper.doi:
            parts.append(f"https://doi.org/{paper.doi}")
        return " ".join(parts)

    def format_vancouver(self, paper: Paper, number: int = 1) -> str:
        authors = self._format_authors_vancouver(paper.authors)
        parts = [f"{number}. {authors}."]
        parts.append(paper.title)
        if paper.journal:
            parts.append(f"{paper.journal}.")
        parts.append(str(paper.year))
        if paper.doi:
            parts.append(f"doi: {paper.doi}")
        return " ".join(parts)

    def format_bibtex(self, paper: Paper, key: str = "") -> str:
        key = key or self._generate_key(paper)
        lines = [f"@article{{{key},"]
        lines.append(f"  title = {{{paper.title}}},")
        if paper.authors:
            authors_str = " and ".join(paper.authors)
            lines.append(f"  author = {{{authors_str}}},")
        if paper.journal:
            lines.append(f"  journal = {{{paper.journal}}},")
        if paper.year:
            lines.append(f"  year = {{{paper.year}}},")
        if paper.doi:
            lines.append(f"  doi = {{{paper.doi}}},")
        if paper.pmid:
            lines.append(f"  pmid = {{{paper.pmid}}},")
        if paper.url:
            lines.append(f"  url = {{{paper.url}}},")
        lines.append("}")
        return "\n".join(lines)

    def generate_bibliography(self, style: str = "apa") -> str:
        citations = sorted(
            self._citations.values(),
            key=lambda c: (c.paper.authors[0] if c.paper.authors else "", c.paper.year),
        )
        entries = []
        for i, citation in enumerate(citations, 1):
            if style == "apa":
                entries.append(self.format_apa(citation.paper))
            elif style == "vancouver":
                entries.append(self.format_vancouver(citation.paper, i))
            elif style == "bibtex":
                entries.append(self.format_bibtex(citation.paper, citation.key))
            else:
                entries.append(self.format_apa(citation.paper))
        return "\n\n".join(entries)

    def deduplicate(self) -> int:
        seen: dict[str, str] = {}
        to_remove = []
        for key, citation in self._citations.items():
            dedup_key = self._dedup_key(citation.paper)
            if dedup_key in seen:
                to_remove.append(key)
                logger.debug("Dedup: removing %s (duplicate of %s)", key, seen[dedup_key])
            else:
                seen[dedup_key] = key

        for key in to_remove:
            del self._citations[key]

        if to_remove:
            logger.info("Deduplicated %d citations", len(to_remove))
        return len(to_remove)

    def build_graph(self) -> CitationGraph:
        graph = CitationGraph()
        for key, citation in self._citations.items():
            graph.nodes[key] = citation

        for key_a, cit_a in self._citations.items():
            for key_b, cit_b in self._citations.items():
                if key_a >= key_b:
                    continue
                if self._are_related(cit_a.paper, cit_b.paper):
                    graph.edges.append((key_a, key_b))
        return graph

    def verify_citations(self) -> list[dict]:
        issues = []
        for key, citation in self._citations.items():
            paper = citation.paper
            if not paper.title:
                issues.append({"key": key, "issue": "missing_title"})
            if not paper.authors:
                issues.append({"key": key, "issue": "missing_authors"})
            if not paper.year:
                issues.append({"key": key, "issue": "missing_year"})
            if not paper.doi and not paper.pmid and not paper.arxiv_id:
                issues.append({"key": key, "issue": "missing_identifier"})
        if issues:
            logger.warning("Citation verification: %d issues found", len(issues))
        return issues

    def _generate_key(self, paper: Paper) -> str:
        first_author = paper.authors[0].split()[-1] if paper.authors else "unknown"
        first_author = re.sub(r"[^a-zA-Z]", "", first_author).lower()
        year = paper.year or "xxxx"
        title_word = ""
        if paper.title:
            words = re.findall(r"[a-zA-Z]+", paper.title)
            for w in words:
                if w.lower() not in {"a", "an", "the", "of", "in", "on", "for", "and", "with"}:
                    title_word = w.lower()
                    break
        return f"{first_author}{year}{title_word}"

    def _dedup_key(self, paper: Paper) -> str:
        if paper.doi:
            return f"doi:{paper.doi.lower()}"
        if paper.pmid:
            return f"pmid:{paper.pmid}"
        return f"title:{paper.title.lower().strip()[:80]}"

    def _are_related(self, a: Paper, b: Paper) -> bool:
        a_kw = set(k.lower() for k in a.keywords + a.mesh_terms)
        b_kw = set(k.lower() for k in b.keywords + b.mesh_terms)
        overlap = a_kw & b_kw
        if len(overlap) >= 2:
            return True
        return bool(a.doi and a.doi == b.doi)

    def _format_authors_apa(self, authors: list[str]) -> str:
        if not authors:
            return "[No authors]"
        if len(authors) == 1:
            return authors[0]
        if len(authors) == 2:
            return f"{authors[0]} & {authors[1]}"
        return f"{', '.join(authors[:3])} et al."

    def _format_authors_vancouver(self, authors: list[str]) -> str:
        if not authors:
            return "[No authors]"
        formatted = []
        for author in authors[:6]:
            parts = author.split()
            if len(parts) >= 2:
                formatted.append(f"{parts[0]} {''.join(p[0] for p in parts[1:])}")
            else:
                formatted.append(author)
        if len(authors) > 6:
            formatted.append("et al")
        return ", ".join(formatted)
