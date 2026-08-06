from __future__ import annotations

from fusion_science.core.gateway import LLMGateway
from fusion_science.literature.citation import CitationGraph, CitationManager
from fusion_science.literature.math_explainer import FORMULA_PATTERNS, MathExplainer
from fusion_science.literature.paper import PaperDraft, PaperGenerator
from fusion_science.literature.search import Paper


class TestMultiModelSwitch:
    def test_default_model(self):
        gw = LLMGateway(model="test-model")
        assert gw.model == "test-model"
        assert gw.default_model == "test-model"

    def test_set_model(self):
        gw = LLMGateway(model="model-a")
        gw.set_model("model-b")
        assert gw.model == "model-b"
        assert gw.default_model == "model-a"

    def test_model_roles_default(self):
        gw = LLMGateway(model="test-model")
        roles = gw.get_model_roles()
        assert roles["reasoning"] == "test-model"
        assert roles["summarization"] == "test-model"
        assert roles["code"] == "test-model"

    def test_set_model_for_role(self):
        gw = LLMGateway(model="default")
        gw.set_model_for_role("reasoning", "big-model")
        assert gw.get_model_for_role("reasoning") == "big-model"
        assert gw.get_model_for_role("summarization") == "default"

    def test_get_model_for_role_unknown(self):
        gw = LLMGateway(model="default")
        assert gw.get_model_for_role("nonexistent") == "default"

    def test_get_available_models_empty(self):
        gw = LLMGateway()
        assert gw.get_available_models() == []

    def test_chat_with_model_override(self):
        gw = LLMGateway(model="default")
        payload_args = gw.chat.__code__.co_varnames
        assert "model" in payload_args


class TestMathExplainer:
    def test_explain_pvalue(self):
        ex = MathExplainer()
        result = ex.explain("p < 0.001")
        assert result.name == "p-value"
        assert "significance" in result.explanation.lower()

    def test_explain_correlation(self):
        ex = MathExplainer()
        result = ex.explain("r = 0.85")
        assert result.name == "correlation coefficient"

    def test_explain_odds_ratio(self):
        ex = MathExplainer()
        result = ex.explain("OR = 2.5")
        assert result.name == "odds ratio"

    def test_explain_auc(self):
        ex = MathExplainer()
        result = ex.explain("AUC = 0.92")
        assert result.name == "AUC (Area Under Curve)"

    def test_explain_sample_size(self):
        ex = MathExplainer()
        result = ex.explain("n = 150")
        assert result.name == "sample size"

    def test_explain_cohen_d(self):
        ex = MathExplainer()
        result = ex.explain("d = 0.8")
        assert result.name == "Cohen's d"

    def test_explain_generic(self):
        ex = MathExplainer()
        result = ex.explain("x + y = z")
        assert result.name == "mathematical expression"

    def test_explain_latex_symbols(self):
        ex = MathExplainer()
        result = ex.explain("\\alpha + \\beta = \\gamma")
        assert len(result.symbols) == 3
        assert "α" in result.plain_text

    def test_explain_to_dict(self):
        ex = MathExplainer()
        result = ex.explain("p < 0.05")
        d = result.to_dict()
        assert "original" in d
        assert "name" in d
        assert "explanation" in d

    def test_explain_text_inline(self):
        ex = MathExplainer()
        text = "Results showed $r = 0.7$ and $p < 0.01$ significance."
        results = ex.explain_text(text)
        assert len(results) >= 2

    def test_explain_text_pattern_match(self):
        ex = MathExplainer()
        text = "The odds ratio was OR = 3.2 with n = 200 participants."
        results = ex.explain_text(text)
        names = [r.name for r in results]
        assert "odds ratio" in names
        assert "sample size" in names

    async def test_explain_with_llm_no_gateway(self):
        ex = MathExplainer(gateway=None)
        result = await ex.explain_with_llm("p < 0.05")
        assert result.name == "p-value"

    def test_latex_to_plain(self):
        ex = MathExplainer()
        plain = ex._latex_to_plain("\\alpha^2 + \\beta_1")
        assert "α" in plain

    def test_formula_patterns_coverage(self):
        assert len(FORMULA_PATTERNS) >= 12


class TestCitationGraph:
    def _make_paper(self, title, authors=None, year="2024", doi="", keywords=None):
        return Paper(
            title=title,
            authors=authors or ["Author A"],
            year=year,
            doi=doi,
            keywords=keywords or [],
        )

    def test_build_graph(self):
        mgr = CitationManager()
        p1 = self._make_paper("Paper 1", keywords=["cancer", "tp53"])
        p2 = self._make_paper("Paper 2", keywords=["cancer", "therapy"])
        mgr.add_paper(p1)
        mgr.add_paper(p2)
        graph = mgr.build_graph()
        assert isinstance(graph, CitationGraph)
        assert graph.to_dict()["node_count"] == 2

    def test_graph_to_dict(self):
        mgr = CitationManager()
        p1 = self._make_paper("Alpha", keywords=["genomics", "bioinformatics"])
        p2 = self._make_paper("Beta", keywords=["genomics", "bioinformatics"])
        mgr.add_paper(p1)
        mgr.add_paper(p2)
        graph = mgr.build_graph()
        d = graph.to_dict()
        assert "node_count" in d
        assert "edge_count" in d
        assert "nodes" in d
        assert "edges" in d

    def test_graph_related_papers(self):
        mgr = CitationManager()
        p1 = self._make_paper("Related 1", keywords=["breast cancer", "tp53", "mutation"])
        p2 = self._make_paper("Related 2", keywords=["breast cancer", "tp53", "prognosis"])
        mgr.add_paper(p1)
        mgr.add_paper(p2)
        graph = mgr.build_graph()
        assert graph.to_dict()["edge_count"] >= 1

    def test_graph_unrelated_papers(self):
        mgr = CitationManager()
        p1 = self._make_paper("Unrelated 1", keywords=["ecology"])
        p2 = self._make_paper("Unrelated 2", keywords=["economics"])
        mgr.add_paper(p1)
        mgr.add_paper(p2)
        graph = mgr.build_graph()
        assert graph.to_dict()["edge_count"] == 0


class TestPaperGeneratorEnhanced:
    def test_create_paper_imrad(self):
        gen = PaperGenerator()
        draft = gen.create_paper("Test Paper")
        assert draft.title == "Test Paper"
        assert len(draft.sections) == 6
        assert draft.sections[0].heading == "Abstract"

    def test_create_paper_custom_sections(self):
        gen = PaperGenerator()
        draft = gen.create_paper("Custom", sections=["Intro", "Body", "Conclusion"])
        assert len(draft.sections) == 3

    def test_create_paper_with_references(self):
        gen = PaperGenerator()
        papers = [
            Paper(title="Ref 1", authors=["A"], year="2024", journal="Nature"),
            Paper(title="Ref 2", authors=["B"], year="2023", journal="Science"),
        ]
        draft = gen.create_paper("With Refs", papers=papers)
        assert len(draft.references) == 2

    async def test_write_section_no_engine(self):
        gen = PaperGenerator()
        draft = gen.create_paper("Test")
        draft = await gen.write_section(draft, 0, context="test context")
        assert draft.sections[0].content != ""
        assert draft.sections[0].word_count > 0

    async def test_write_section_invalid_index(self):
        gen = PaperGenerator()
        draft = gen.create_paper("Test")
        draft = await gen.write_section(draft, 99)
        assert len(draft.sections) == 6

    def test_check_section_balance(self):
        gen = PaperGenerator()
        draft = gen.create_paper("Test")
        draft.sections[0].word_count = 100
        draft.sections[1].word_count = 0
        warnings = PaperGenerator.check_section_balance(draft)
        assert any("empty" in w.lower() for w in warnings)

    def test_generate_figure_legend(self):
        legend = PaperGenerator.generate_figure_legend("bar chart", "Gene expression levels", "t-test, p < 0.05")
        assert "Gene expression" in legend
        assert "p < 0.05" in legend

    def test_generate_methods_from_code(self):
        code = "import pandas as pd\nfrom scipy import stats\nresult = stats.ttest_ind(a, b)"
        methods = PaperGenerator.generate_methods_from_code(code)
        assert "pandas" in methods
        assert "scipy" in methods
        assert "t-test" in methods.lower()

    def test_format_reference_apa(self):
        gen = PaperGenerator()
        p = Paper(title="Test Paper", authors=["Smith J", "Doe A"], year="2024", journal="Nature", doi="10.1234/test")
        ref = gen._format_reference(p, style="apa")
        assert "Smith" in ref
        assert "2024" in ref
        assert "Nature" in ref

    def test_paper_draft_status(self):
        draft = PaperDraft(title="Test")
        assert draft.status == "draft"


class TestConfigModelRoles:
    def test_config_has_model_role_fields(self):
        from fusion_science.config import ScienceConfig

        config = ScienceConfig()
        assert hasattr(config, "model_reasoning")
        assert hasattr(config, "model_summarization")
        assert hasattr(config, "model_code")
        assert config.model_reasoning == ""
        assert config.model_summarization == ""
        assert config.model_code == ""

    def test_config_model_role_from_dict(self):
        from fusion_science.config import ScienceConfig

        config = ScienceConfig(model_reasoning="big-model", model_code="code-model")
        assert config.model_reasoning == "big-model"
        assert config.model_code == "code-model"
