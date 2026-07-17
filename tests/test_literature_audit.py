"""Tests for the literature and audit modules."""

from __future__ import annotations

import pytest
import json
import tempfile
import os
from pathlib import Path

from fusion_science.literature.search import LiteratureSearch, Paper, SearchResult
from fusion_science.literature.review import LiteratureReviewer, LiteratureReview, ReviewSection
from fusion_science.literature.paper import PaperGenerator, PaperDraft, PaperSection
from fusion_science.audit.tracker import TraceRecorder, TraceSession, TraceEntry
from fusion_science.audit.provenance import ProvenanceTracker, ProvenanceNode, ProvenanceGraph


# =========================================================================
# Literature Tests
# =========================================================================

class TestPaper:
    """Test the Paper dataclass."""

    def test_default_paper(self):
        paper = Paper(title="Test Paper")
        assert paper.title == "Test Paper"
        assert paper.authors == []
        assert paper.abstract == ""
        assert paper.doi == ""
        assert paper.relevance_score == 0.0

    def test_full_paper(self):
        paper = Paper(
            title="CRISPR-Cas9",
            authors=["Doudna J", "Charpentier E"],
            abstract="A gene editing tool",
            journal="Science",
            year="2012",
            doi="10.1126/science.1225829",
            pmid="22745249",
            source="PubMed",
            keywords=["CRISPR", "gene editing"],
        )
        assert paper.title == "CRISPR-Cas9"
        assert len(paper.authors) == 2
        assert paper.doi == "10.1126/science.1225829"
        assert paper.pmid == "22745249"


class TestSearchResult:
    """Test the SearchResult dataclass."""

    def test_empty_result(self):
        result = SearchResult(query="cancer")
        assert result.query == "cancer"
        assert result.papers == []
        assert result.total_count == 0

    def test_with_papers(self):
        papers = [Paper(title="Paper 1"), Paper(title="Paper 2")]
        result = SearchResult(query="test", papers=papers, total_count=2)
        assert len(result.papers) == 2
        assert result.total_count == 2


class TestLiteratureSearch:
    """Test the LiteratureSearch module."""

    def test_extract_pmids(self):
        pmids = LiteratureSearch.extract_pmids("This paper (PMID: 12345678) shows...")
        assert "12345678" in pmids

    def test_extract_dois(self):
        dois = LiteratureSearch.extract_dois("See doi: 10.1038/s41586-020-2008-3")
        assert "10.1038/s41586-020-2008-3" in dois

    def test_extract_multiple_pmids(self):
        text = "Study A (PMID: 11111111) and Study B (PMID: 22222222)"
        pmids = LiteratureSearch.extract_pmids(text)
        assert len(pmids) == 2
        assert "11111111" in pmids
        assert "22222222" in pmids


class TestLiteratureReviewer:
    """Test the LiteratureReviewer."""

    def test_reviewer_init(self):
        reviewer = LiteratureReviewer()
        assert reviewer._themes == {}

    def test_analyze_papers_empty(self):
        reviewer = LiteratureReviewer()
        review = reviewer.analyze_papers([], "test query")
        assert review.title == "Literature Review: test query"
        assert len(review.papers_reviewed) == 0
        assert review.summary == "No papers were included in this review."

    def test_analyze_papers_with_data(self):
        reviewer = LiteratureReviewer()
        papers = [
            Paper(title="Cancer Genomics", keywords=["genomics", "cancer"]),
            Paper(title="Drug Discovery", keywords=["drug", "pharma"]),
        ]
        review = reviewer.analyze_papers(papers, "cancer")
        assert len(review.papers_reviewed) == 2
        assert len(review.sections) >= 3


class TestPaperGenerator:
    """Test the PaperGenerator."""

    def test_generator_init(self):
        gen = PaperGenerator()
        assert gen.engine is None

    def test_create_paper_imrad(self):
        gen = PaperGenerator()
        paper = gen.create_paper("Test Study")
        assert paper.title == "Test Study"
        assert len(paper.sections) == len(gen.IMRAD_SECTIONS)
        assert paper.sections[0].heading == "Abstract"

    def test_create_paper_custom_sections(self):
        gen = PaperGenerator()
        sections = ["Introduction", "Methods", "Results"]
        paper = gen.create_paper("Custom Study", sections=sections)
        assert len(paper.sections) == 3
        assert paper.sections[0].heading == "Introduction"

    def test_generate_figure_legend(self):
        legend = PaperGenerator.generate_figure_legend(
            "bar chart", "Comparison of expression levels", "t-test, p<0.05"
        )
        assert "Figure X" in legend
        assert "bar chart" in legend or "Comparison" in legend
        assert "t-test" in legend

    def test_generate_methods_from_code(self):
        code = """
import pandas as pd
import numpy as np
from scipy import stats
result = stats.ttest_ind(group1, group2)
"""
        methods = PaperGenerator.generate_methods_from_code(code, language="python")
        assert "Methods" in methods
        assert "pandas" in methods
        assert "numpy" in methods
        assert "t-tests" in methods or "ttest" in methods


# =========================================================================
# Audit Tests
# =========================================================================

class TestTraceRecorder:
    """Test the TraceRecorder."""

    def test_recorder_init(self, tmp_path):
        recorder = TraceRecorder(storage_dir=str(tmp_path))
        assert recorder._session is None

    def test_start_session(self, tmp_path):
        recorder = TraceRecorder(storage_dir=str(tmp_path))
        session_id = recorder.start_session({"task": "test"})
        assert session_id.startswith("trace_")
        assert recorder._session is not None
        assert recorder._session.metadata["task"] == "test"

    def test_record_entry(self, tmp_path):
        recorder = TraceRecorder(storage_dir=str(tmp_path))
        recorder.start_session()
        entry_id = recorder.record(
            operation="db_query",
            source="test",
            description="Test query",
            success=True,
        )
        assert entry_id.startswith("entry_")
        assert len(recorder._session.entries) == 1

    def test_record_db_query(self, tmp_path):
        recorder = TraceRecorder(storage_dir=str(tmp_path))
        recorder.start_session()
        entry_id = recorder.record_db_query(
            source="test", database="pubmed",
            query="cancer", result_count=10,
        )
        assert entry_id is not None
        entries = recorder.get_entries(operation="db_query")
        assert len(entries) == 1

    def test_get_session_summary(self, tmp_path):
        recorder = TraceRecorder(storage_dir=str(tmp_path))
        recorder.start_session()
        recorder.record(operation="test", source="test", description="op1")
        recorder.record(operation="test", source="test", description="op2")
        summary = recorder.get_session_summary()
        assert summary["total_entries"] == 2

    def test_end_session(self, tmp_path):
        recorder = TraceRecorder(storage_dir=str(tmp_path))
        recorder.start_session()
        recorder.record(operation="test", source="test", description="op")
        session = recorder.end_session()
        assert session.status == "completed"
        assert session.end_time > 0

    def test_export_json(self, tmp_path):
        recorder = TraceRecorder(storage_dir=str(tmp_path))
        recorder.start_session()
        recorder.record(operation="test", source="test", description="op")
        json_str = recorder.export_json()
        data = json.loads(json_str)
        assert data["status"] == "active"
        assert len(data["entries"]) == 1


class TestProvenanceTracker:
    """Test the ProvenanceTracker."""

    def test_tracker_init(self, tmp_path):
        tracker = ProvenanceTracker(storage_dir=str(tmp_path))
        assert tracker._graph is None

    def test_start_tracking(self, tmp_path):
        tracker = ProvenanceTracker(storage_dir=str(tmp_path))
        name = tracker.start_tracking("test", "A test provenance graph")
        assert name == "test"
        assert tracker._graph is not None

    def test_add_source(self, tmp_path):
        tracker = ProvenanceTracker(storage_dir=str(tmp_path))
        tracker.start_tracking("test")
        node_id = tracker.add_source("Query PubMed", "db_query", {"query": "cancer"})
        assert node_id.startswith("src_")
        assert len(tracker._graph.nodes) == 1

    def test_add_transformation(self, tmp_path):
        tracker = ProvenanceTracker(storage_dir=str(tmp_path))
        tracker.start_tracking("test")
        src_id = tracker.add_source("Source data", "upload")
        tx_id = tracker.add_transformation("Analysis", [src_id])
        assert tx_id.startswith("tx_")
        # Check parent-child relationship
        parent = tracker._graph.nodes[src_id]
        assert tx_id in parent.outputs

    def test_add_output(self, tmp_path):
        tracker = ProvenanceTracker(storage_dir=str(tmp_path))
        tracker.start_tracking("test")
        src_id = tracker.add_source("Data", "db_query")
        tx_id = tracker.add_transformation("Process", [src_id])
        out_id = tracker.add_output("Figure", [tx_id], "chart")
        assert out_id.startswith("out_")

    def test_get_lineage(self, tmp_path):
        tracker = ProvenanceTracker(storage_dir=str(tmp_path))
        tracker.start_tracking("test")
        src = tracker.add_source("Source", "db")
        tx = tracker.add_transformation("Transform", [src])
        out = tracker.add_output("Output", [tx], "figure")
        lineage = tracker.get_lineage(out)
        # Should include source, transform, and output
        assert len(lineage) >= 3

    def test_export_json(self, tmp_path):
        tracker = ProvenanceTracker(storage_dir=str(tmp_path))
        tracker.start_tracking("test")
        tracker.add_source("Source", "db")
        json_str = tracker.export_json()
        data = json.loads(json_str)
        assert data["name"] == "test"
        assert data["node_count"] == 1


class TestProvenanceNode:
    """Test the ProvenanceNode dataclass."""

    def test_source_node(self):
        node = ProvenanceNode(id="src_1", type="source", label="PubMed Query", timestamp=1000.0)
        assert node.id == "src_1"
        assert node.type == "source"
        assert node.inputs == []
        assert node.outputs == []

    def test_transformation_node(self):
        node = ProvenanceNode(
            id="tx_1", type="transformation", label="Analysis",
            timestamp=1000.0, inputs=["src_1"], outputs=["out_1"],
        )
        assert node.inputs == ["src_1"]
        assert node.outputs == ["out_1"]