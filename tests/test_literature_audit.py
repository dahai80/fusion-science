"""Tests for the literature and audit modules."""

from __future__ import annotations

import json

from fusion_science.audit.provenance import ProvenanceNode, ProvenanceTracker
from fusion_science.audit.tracker import TraceRecorder
from fusion_science.database.aggregator import AggregatedResult, DatabaseAggregator
from fusion_science.literature.citation import Citation, CitationGraph, CitationManager
from fusion_science.literature.extractor import PICO, LiteratureExtractor, StructuredExtraction
from fusion_science.literature.paper import PaperGenerator
from fusion_science.literature.reader import LiteratureReader, PaperReading, SectionSummary
from fusion_science.literature.review import LiteratureReviewer
from fusion_science.literature.search import LiteratureSearch, Paper, PRISMAFlow, SearchPreset, SearchResult
from fusion_science.literature.synthesizer import ConsensusAnalysis, Contradiction, Finding, LiteratureSynthesizer

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

    async def test_analyze_papers_empty(self):
        reviewer = LiteratureReviewer()
        review = await reviewer.analyze_papers([], "test query")
        assert review.title == "Literature Review: test query"
        assert len(review.papers_reviewed) == 0
        assert review.summary == "No papers were included in this review."

    async def test_analyze_papers_with_data(self):
        reviewer = LiteratureReviewer()
        papers = [
            Paper(title="Cancer Genomics", keywords=["genomics", "cancer"]),
            Paper(title="Drug Discovery", keywords=["drug", "pharma"]),
        ]
        review = await reviewer.analyze_papers(papers, "cancer")
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


# =========================================================================
# Phase 3 Tests — LiteratureReader, Extractor, Synthesizer, Citation, Aggregator
# =========================================================================


class TestLiteratureReader:
    def test_reader_init_no_gateway(self):
        reader = LiteratureReader(gateway=None)
        assert reader._gateway is None

    async def test_read_paper_stub(self):
        reader = LiteratureReader(gateway=None)
        paper = Paper(title="CRISPR Gene Editing", abstract="CRISPR-Cas9 is a tool")
        reading = await reader.read_paper(paper)
        assert reading.title == "CRISPR Gene Editing"
        assert reading.tldr
        assert reading.reading_quality == 0.0

    async def test_read_papers_stub(self):
        reader = LiteratureReader(gateway=None)
        papers = [
            Paper(title="Paper A", abstract="Abstract A"),
            Paper(title="Paper B", abstract="Abstract B"),
        ]
        readings = await reader.read_papers(papers, max_concurrent=2)
        assert len(readings) == 2
        assert all(r.reading_quality == 0.0 for r in readings)

    def test_section_summary_fields(self):
        ss = SectionSummary(
            section_name="Methods",
            summary="Used RCT",
            key_points=["randomized", "double-blind"],
            confidence=0.9,
        )
        assert ss.section_name == "Methods"
        assert len(ss.key_points) == 2
        assert ss.confidence == 0.9

    def test_paper_reading_to_dict(self):
        pr = PaperReading(
            paper_id="p1",
            title="Test",
            tldr="Short",
            overall_summary="Longer",
            section_summaries=[],
            key_findings=["f1"],
            methodology_assessment="Good",
            strengths=[],
            weaknesses=[],
            reading_quality=0.0,
        )
        d = pr.to_dict()
        assert d["paper_id"] == "p1"
        assert d["reading_quality"] == 0.0


class TestLiteratureExtractor:
    def test_extractor_init_no_gateway(self):
        ext = LiteratureExtractor(gateway=None)
        assert ext._gateway is None

    async def test_extract_stub(self):
        ext = LiteratureExtractor(gateway=None)
        paper = Paper(
            title="RCT of Drug X",
            abstract="We enrolled 200 patients (p<0.01) to test Drug X vs placebo.",
        )
        result = await ext.extract(paper)
        assert isinstance(result, StructuredExtraction)
        assert result.study_type in ("RCT", "meta_analysis", "cohort", "review", "other")

    async def test_extract_pico_stub(self):
        ext = LiteratureExtractor(gateway=None)
        paper = Paper(
            title="Drug X in hypertension patients",
            abstract="200 hypertensive patients received Drug X vs placebo",
        )
        pico = await ext.extract_pico(paper)
        assert isinstance(pico, PICO)

    async def test_extract_batch_stub(self):
        ext = LiteratureExtractor(gateway=None)
        papers = [
            Paper(title="Study 1", abstract="100 patients with diabetes"),
            Paper(title="Study 2", abstract="50 patients with asthma"),
        ]
        results = await ext.extract_batch(papers)
        assert len(results) == 2

    def test_structured_extraction_to_dict(self):
        se = StructuredExtraction(
            study_type="RCT",
            pico=PICO(population="adults", intervention="Drug X", comparator="placebo", outcome="BP reduction"),
            sample_size=200,
        )
        d = se.to_dict()
        assert d["study_type"] == "RCT"
        assert d["pico"]["population"] == "adults"

    def test_classify_study_type(self):
        ext = LiteratureExtractor(gateway=None)
        assert ext._classify_study_type("A randomized controlled trial of...") == "RCT"
        assert ext._classify_study_type("A systematic review and meta-analysis") == "meta_analysis"
        assert ext._classify_study_type("A cohort study of...") == "cohort"

    def test_extract_sample_size(self):
        ext = LiteratureExtractor(gateway=None)
        assert ext._extract_sample_size("We enrolled 500 patients") == 500
        assert ext._extract_sample_size("n=120 participants") == 120


class TestLiteratureSynthesizer:
    def test_synthesizer_init_no_gateway(self):
        syn = LiteratureSynthesizer(gateway=None, extractor=None)
        assert syn._gateway is None

    async def test_synthesize_rule_based(self):
        syn = LiteratureSynthesizer(gateway=None, extractor=None)
        papers = [
            Paper(title="CRISPR works", abstract="CRISPR is effective", year="2020"),
            Paper(title="CRISPR concerns", abstract="CRISPR has off-target effects", year="2021"),
            Paper(title="CRISPR improved", abstract="Improved CRISPR reduces off-target", year="2022"),
        ]
        result = await syn.synthesize(papers, topic="CRISPR")
        assert isinstance(result, ConsensusAnalysis)
        assert result.total_papers == 3
        assert -1.0 <= result.consensus_score <= 1.0

    def test_consensus_analysis_to_dict(self):
        ca = ConsensusAnalysis(
            topic="test",
            total_papers=5,
            supporting=3,
            contradicting=1,
            inconclusive=1,
            consensus_score=0.4,
            key_findings=[Finding(statement="finding1", confidence=0.8)],
            contradictions=[],
            research_gaps=["gap1"],
            trends=[],
        )
        d = ca.to_dict()
        assert d["total_papers"] == 5
        assert d["consensus_score"] == 0.4
        assert len(d["key_findings"]) == 1

    def test_contradiction_dataclass(self):
        c = Contradiction(
            topic="Drug efficacy",
            position_a="Drug works",
            position_b="Drug fails",
            position_a_papers=["p1"],
            position_b_papers=["p2"],
            possible_reason="Different populations",
        )
        assert c.position_a == "Drug works"
        assert c.position_b == "Drug fails"

    def test_finding_dataclass(self):
        f = Finding(
            statement="CRISPR is effective",
            supporting_papers=["p1", "p2"],
            contradicting_papers=[],
            confidence=0.8,
        )
        assert f.confidence == 0.8


class TestCitationManager:
    def test_manager_init(self):
        mgr = CitationManager()
        assert len(mgr._citations) == 0

    def test_add_paper(self):
        mgr = CitationManager()
        paper = Paper(
            title="CRISPR-Cas9",
            authors=["Doudna J", "Charpentier E"],
            year="2012",
            doi="10.1126/science.1225829",
        )
        citation = mgr.add_paper(paper)
        assert "2012" in citation.key
        assert citation.paper.title == "CRISPR-Cas9"
        assert len(mgr._citations) == 1

    def test_add_papers(self):
        mgr = CitationManager()
        papers = [
            Paper(title="Paper A", authors=["Smith A"], year="2020"),
            Paper(title="Paper B", authors=["Jones B"], year="2021"),
        ]
        cites = mgr.add_papers(papers)
        assert len(cites) == 2
        assert len(mgr._citations) == 2

    def test_get_citation(self):
        mgr = CitationManager()
        paper = Paper(title="Test", authors=["Author A"], year="2023")
        citation = mgr.add_paper(paper)
        cite = mgr.get_citation(citation.key)
        assert cite is not None
        assert cite.paper.title == "Test"

    def test_get_all_citations(self):
        mgr = CitationManager()
        mgr.add_paper(Paper(title="P1", authors=["A"], year="2020"))
        mgr.add_paper(Paper(title="P2", authors=["B"], year="2021"))
        all_cites = mgr.get_all_citations()
        assert len(all_cites) == 2

    def test_remove_citation(self):
        mgr = CitationManager()
        citation = mgr.add_paper(Paper(title="To Remove", authors=["C"], year="2022"))
        assert len(mgr._citations) == 1
        mgr.remove_citation(citation.key)
        assert len(mgr._citations) == 0

    def test_format_apa(self):
        mgr = CitationManager()
        paper = Paper(
            title="Gene Editing in 2020",
            authors=["Smith J", "Doe A"],
            year="2020",
            journal="Nature",
        )
        apa = mgr.format_apa(paper)
        assert "Smith" in apa
        assert "2020" in apa

    def test_format_bibtex(self):
        mgr = CitationManager()
        paper = Paper(
            title="Gene Editing",
            authors=["Smith J"],
            year="2020",
            journal="Nature",
        )
        bibtex = mgr.format_bibtex(paper)
        assert "@article" in bibtex
        assert "Gene Editing" in bibtex

    def test_format_vancouver(self):
        mgr = CitationManager()
        paper = Paper(
            title="Gene Editing",
            authors=["Smith J", "Doe A"],
            year="2020",
            journal="Nature",
        )
        van = mgr.format_vancouver(paper)
        assert "Smith" in van

    def test_generate_bibliography(self):
        mgr = CitationManager()
        mgr.add_paper(Paper(title="P1", authors=["A"], year="2020", journal="J1"))
        mgr.add_paper(Paper(title="P2", authors=["B"], year="2021", journal="J2"))
        bib = mgr.generate_bibliography(style="apa")
        assert "P1" in bib
        assert "P2" in bib

    def test_deduplicate(self):
        mgr = CitationManager()
        p = Paper(title="Same Paper", authors=["Same Author"], year="2020", doi="10.1234/test")
        mgr.add_paper(p, key="key1")
        mgr.add_paper(p, key="key2")
        assert len(mgr._citations) == 2
        removed = mgr.deduplicate()
        assert removed >= 1

    def test_citation_to_dict(self):
        paper = Paper(title="Test", authors=["A"], year="2020")
        cite = Citation(key="test_2020_test", paper=paper)
        d = cite.to_dict()
        assert d["key"] == "test_2020_test"

    def test_citation_graph_to_dict(self):
        p1 = Paper(title="P1", authors=["A"], year="2020")
        p2 = Paper(title="P2", authors=["B"], year="2021")
        c1 = Citation(key="n1", paper=p1)
        c2 = Citation(key="n2", paper=p2)
        graph = CitationGraph(nodes={"n1": c1, "n2": c2}, edges=[("n1", "n2")])
        d = graph.to_dict()
        assert d["node_count"] == 2
        assert d["edge_count"] == 1


class TestSearchPreset:
    def test_preset_values(self):
        assert SearchPreset.QUICK.value == "quick"
        assert SearchPreset.PROFESSIONAL.value == "pro"
        assert SearchPreset.DEEP.value == "deep"

    def test_prisma_flow_defaults(self):
        pf = PRISMAFlow()
        assert pf.identification == 0
        assert pf.screening == 0
        assert pf.included == 0


class TestAggregatedResult:
    def test_defaults(self):
        ar = AggregatedResult(query="test")
        assert ar.query == "test"
        assert ar.databases_used == []
        assert ar.merged_items == []
        assert ar.total_count == 0

    def test_to_dict(self):
        ar = AggregatedResult(query="cancer", databases_used=["pubmed"], total_count=5)
        d = ar.to_dict()
        assert d["query"] == "cancer"
        assert d["databases_used"] == ["pubmed"]


class TestDatabaseAggregator:
    def test_init_defaults(self):
        agg = DatabaseAggregator()
        assert len(agg._databases) == 5
        assert agg._max_concurrent == 5

    def test_init_custom_dbs(self):
        agg = DatabaseAggregator(databases=["pubmed", "uniprot"])
        assert len(agg._databases) == 2

    async def test_search_unknown_db(self):
        agg = DatabaseAggregator(databases=["unknown_db"])
        result = await agg.search("test query")
        assert "unknown_db" in result.errors
        assert result.total_count == 0
