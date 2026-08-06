"""Literature search, reading, extraction, synthesis, review, and citation management."""

from __future__ import annotations

from .citation import Citation, CitationGraph, CitationManager
from .extractor import PICO, LiteratureExtractor, StructuredExtraction
from .reader import LiteratureReader, PaperReading, SectionSummary
from .review import LiteratureReview, LiteratureReviewer, ReviewSection
from .search import LiteratureSearch, Paper, PRISMAFlow, SearchPreset, SearchResult
from .synthesizer import ConsensusAnalysis, Contradiction, Finding, LiteratureSynthesizer

__all__ = [
    "LiteratureSearch",
    "Paper",
    "PRISMAFlow",
    "SearchResult",
    "SearchPreset",
    "LiteratureReader",
    "PaperReading",
    "SectionSummary",
    "LiteratureExtractor",
    "PICO",
    "StructuredExtraction",
    "LiteratureSynthesizer",
    "ConsensusAnalysis",
    "Contradiction",
    "Finding",
    "LiteratureReview",
    "LiteratureReviewer",
    "ReviewSection",
    "CitationManager",
    "Citation",
    "CitationGraph",
]
