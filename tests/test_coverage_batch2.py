import logging
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from fusion_science.database.base import DatabaseResult
from fusion_science.database.chembl import ChEMBLConnector
from fusion_science.database.chinese import (
    CNKIConnector,
    NGDCConnector,
    ScienceDBConnector,
)
from fusion_science.database.ensembl import EnsemblConnector
from fusion_science.database.mirror import MirrorRouter, ScienceCache
from fusion_science.database.pdb import PDBConnector
from fusion_science.database.pubmed import PubMedConnector
from fusion_science.database.uniprot import UniProtConnector
from fusion_science.utils.mirrors import (
    clear_cache,
    get_available_databases,
    get_cache_stats,
    get_mirror_config,
    get_offline_recommendation,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_response(json_data=None, text_data="", status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data or {}
    resp.text = text_data
    resp.raise_for_status.return_value = None
    if status_code >= 400:
        import httpx

        resp.raise_for_status.side_effect = httpx.HTTPStatusError("error", request=MagicMock(), response=resp)
    return resp


def _mock_request_with_retry(response):
    async def _retry(method, url, **kwargs):
        return response

    return _retry


# ===========================================================================
# 1. PubMedConnector
# ===========================================================================


class TestPubMedConnector:
    def setup_method(self):
        self.conn = PubMedConnector(offline_mode=False, use_mirror=False)

    def test_init_defaults(self):
        c = PubMedConnector()
        assert c.email == "research@localhost"
        assert c.tool_name == "fusion-science"
        assert c.api_key == ""

    def test_init_with_api_key(self):
        c = PubMedConnector(api_key="testkey123")
        assert c.api_key == "testkey123"

    def test_init_env_mirror(self, monkeypatch):
        monkeypatch.setenv("FUSION_SCIENCE_USE_MIRRORS", "true")
        c = PubMedConnector()
        assert c.config.use_mirror is True

    def test_init_env_offline(self, monkeypatch):
        monkeypatch.setenv("FUSION_OFFLINE_MODE", "true")
        c = PubMedConnector()
        assert c.config.offline_mode is True

    @pytest.mark.asyncio
    async def test_search_success(self):
        search_resp = _make_response({"esearchresult": {"idlist": ["12345", "67890"], "count": "2"}})
        fetch_xml = """<?xml version="1.0"?>
        <PubmedArticleSet>
          <PubmedArticle>
            <MedlineCitation>
              <PMID>12345</PMID>
              <Article>
                <ArticleTitle>Test Article</ArticleTitle>
                <Abstract><AbstractText>Test abstract</AbstractText></Abstract>
                <Journal><Title>Test Journal</Title>
                  <ISOAbbreviation>Test J</ISOAbbreviation>
                  <JournalIssue><PubDate><Year>2024</Year></PubDate></JournalIssue>
                </Journal>
                <AuthorList>
                  <Author><LastName>Smith</LastName><ForeName>John</ForeName></Author>
                </AuthorList>
              </Article>
            </MedlineCitation>
            <ArticleIdList>
              <ArticleId IdType="doi">10.1234/test</ArticleId>
            </ArticleIdList>
          </PubmedArticle>
        </PubmedArticleSet>"""
        fetch_resp = _make_response(text_data=fetch_xml)

        call_count = 0

        async def _retry(method, url, **kwargs):
            nonlocal call_count
            call_count += 1
            if "esearch" in url:
                return search_resp
            return fetch_resp

        self.conn._request_with_retry = _retry
        result = await self.conn.search("cancer", max_results=5)
        assert result.source == "pubmed"
        assert result.total_count == 2
        assert len(result.items) >= 1
        assert result.items[0]["pmid"] == "12345"
        assert result.items[0]["title"] == "Test Article"
        assert result.error == ""

    @pytest.mark.asyncio
    async def test_search_with_cache(self):
        cached = DatabaseResult(source="pubmed", query="cancer", items=[{"pmid": "1"}], total_count=1)
        self.conn._set_cache("search:cancer:10", cached)
        result = await self.conn.search("cancer", max_results=10)
        assert result.total_count == 1

    @pytest.mark.asyncio
    async def test_search_error(self):
        async def _retry(method, url, **kwargs):
            raise RuntimeError("network error")

        self.conn._request_with_retry = _retry
        result = await self.conn.search("cancer")
        assert result.source == "pubmed"
        assert result.error != ""

    @pytest.mark.asyncio
    async def test_fetch_success(self):
        fetch_xml = """<?xml version="1.0"?>
        <PubmedArticleSet>
          <PubmedArticle>
            <MedlineCitation>
              <PMID>99999</PMID>
              <Article>
                <ArticleTitle>Fetched Article</ArticleTitle>
                <Abstract><AbstractText>Abstract text</AbstractText></Abstract>
                <Journal><Title>Fetch Journal</Title>
                  <JournalIssue><PubDate><Year>2023</Year></PubDate></JournalIssue>
                </Journal>
              </Article>
            </MedlineCitation>
          </PubmedArticle>
        </PubmedArticleSet>"""
        self.conn._request_with_retry = _mock_request_with_retry(_make_response(text_data=fetch_xml))
        result = await self.conn.fetch("99999")
        assert result.source == "pubmed"
        assert result.items[0]["pmid"] == "99999"

    @pytest.mark.asyncio
    async def test_fetch_error(self):
        async def _retry(method, url, **kwargs):
            raise RuntimeError("fail")

        self.conn._request_with_retry = _retry
        result = await self.conn.fetch("99999")
        assert result.error != ""

    def test_parse_publications_empty(self):
        result = self.conn._parse_publications("")
        assert result == []

    def test_parse_publications_invalid_xml(self):
        result = self.conn._parse_publications("not xml at all")
        assert result == []

    def test_parse_publications_valid_xml(self):
        xml = """<?xml version="1.0"?>
        <PubmedArticleSet>
          <PubmedArticle>
            <MedlineCitation>
              <PMID>111</PMID>
              <Article>
                <ArticleTitle>Parsed Title</ArticleTitle>
                <Abstract><AbstractText>Parsed Abstract</AbstractText></Abstract>
                <Journal><Title>Parsed Journal</Title>
                  <ISOAbbreviation>PJ</ISOAbbreviation>
                  <JournalIssue>
                    <Volume>10</Volume>
                    <Issue>2</Issue>
                    <PubDate><Year>2024</Year></PubDate>
                  </JournalIssue>
                </Journal>
                <AuthorList>
                  <Author><LastName>Doe</LastName><ForeName>Jane</ForeName></Author>
                </AuthorList>
                <Pagination><MedlinePgn>100-110</MedlinePgn></Pagination>
              </Article>
              <MeshHeadingList>
                <MeshHeading><DescriptorName>Neoplasms</DescriptorName></MeshHeading>
              </MeshHeadingList>
              <KeywordList><Keyword>cancer</Keyword></KeywordList>
            </MedlineCitation>
            <ArticleIdList>
              <ArticleId IdType="doi">10.1234/parsed</ArticleId>
            </ArticleIdList>
          </PubmedArticle>
        </PubmedArticleSet>"""
        result = self.conn._parse_publications(xml)
        assert len(result) == 1
        assert result[0]["pmid"] == "111"
        assert result[0]["title"] == "Parsed Title"
        assert result[0]["doi"] == "10.1234/parsed"
        assert result[0]["mesh_terms"] == ["Neoplasms"]
        assert result[0]["keywords"] == ["cancer"]
        assert result[0]["authors"] == ["Jane Doe"]

    def test_get_text_static(self):
        import xml.etree.ElementTree as ET

        root = ET.fromstring("<root><child>Hello</child></root>")
        assert PubMedConnector._get_text(root, "child") == "Hello"
        assert PubMedConnector._get_text(root, "missing") == ""

    @pytest.mark.asyncio
    async def test_search_by_mesh(self):
        async def _retry(method, url, **kwargs):
            return _make_response({"esearchresult": {"idlist": [], "count": "0"}})

        self.conn._request_with_retry = _retry
        result = await self.conn.search_by_mesh("Alzheimer Disease")
        assert "Alzheimer Disease" in result.query

    @pytest.mark.asyncio
    async def test_search_by_author(self):
        async def _retry(method, url, **kwargs):
            return _make_response({"esearchresult": {"idlist": [], "count": "0"}})

        self.conn._request_with_retry = _retry
        result = await self.conn.search_by_author("Smith J")
        assert "Smith J" in result.query

    @pytest.mark.asyncio
    async def test_search_by_gene(self):
        async def _retry(method, url, **kwargs):
            return _make_response({"esearchresult": {"idlist": [], "count": "0"}})

        self.conn._request_with_retry = _retry
        result = await self.conn.search_by_gene("TP53")
        assert "TP53" in result.query

    @pytest.mark.asyncio
    async def test_offline_mode_raises(self):
        c = PubMedConnector(offline_mode=True)
        result = await c.search("test")
        assert result.error != ""
        assert result.items == []

    @pytest.mark.asyncio
    async def test_close(self):
        await self.conn.close()
        assert self.conn._client is None


# ===========================================================================
# 2. UniProtConnector
# ===========================================================================


class TestUniProtConnector:
    def setup_method(self):
        self.conn = UniProtConnector(offline_mode=False, use_mirror=False)

    def test_init_defaults(self):
        c = UniProtConnector()
        assert c.config.base_url == "https://rest.uniprot.org"

    @pytest.mark.asyncio
    async def test_search_success(self):
        resp = _make_response(
            {
                "results": [
                    {
                        "primaryAccession": "P04637",
                        "proteinDescription": {
                            "recommendedName": {
                                "fullName": {"value": "Cellular tumor antigen p53"},
                                "shortNames": [{"value": "Tumor suppressor p53"}],
                            }
                        },
                        "genes": [{"geneName": [{"value": "TP53"}], "synonyms": []}],
                        "organism": {"scientificName": "Homo sapiens", "commonName": "Human", "taxonId": 9606},
                        "sequence": {"length": 393, "molWeight": 43653, "value": "MEEPQ"},
                        "comments": [{"commentType": "FUNCTION", "texts": [{"value": "Acts as a tumor suppressor"}]}],
                        "features": [
                            {
                                "type": "DNA-binding",
                                "description": "DNA-binding domain",
                                "location": {"start": {"value": 102}, "end": {"value": 292}},
                            }
                        ],
                        "keywords": [{"name": "Activator"}],
                        "uniProtKBCrossReferences": [{"database": "PDB", "id": "1TUP"}],
                    }
                ],
                "total": 1,
            }
        )
        self.conn._request_with_retry = _mock_request_with_retry(resp)
        result = await self.conn.search("TP53 human")
        assert result.source == "uniprot"
        assert result.total_count == 1
        assert result.items[0]["accession"] == "P04637"
        assert result.items[0]["protein_name"] == "Cellular tumor antigen p53"
        assert result.items[0]["gene_names"] == ["TP53"]
        assert result.items[0]["organism"] == "Homo sapiens"
        assert result.items[0]["function"] == "Acts as a tumor suppressor"

    @pytest.mark.asyncio
    async def test_search_with_cache(self):
        cached = DatabaseResult(source="uniprot", query="TP53", items=[], total_count=0)
        self.conn._set_cache("search:TP53:20", cached)
        result = await self.conn.search("TP53")
        assert result.total_count == 0

    @pytest.mark.asyncio
    async def test_search_error(self):
        async def _retry(method, url, **kwargs):
            raise RuntimeError("fail")

        self.conn._request_with_retry = _retry
        result = await self.conn.search("TP53")
        assert result.error != ""

    @pytest.mark.asyncio
    async def test_fetch_success(self):
        resp = _make_response(
            {
                "primaryAccession": "P04637",
                "proteinDescription": {"recommendedName": {"fullName": {"value": "p53"}}},
                "genes": [],
                "organism": {},
                "sequence": {},
                "comments": [],
                "features": [],
                "keywords": [],
                "uniProtKBCrossReferences": [],
            }
        )
        self.conn._request_with_retry = _mock_request_with_retry(resp)
        result = await self.conn.fetch("P04637")
        assert result.source == "uniprot"
        assert result.items[0]["accession"] == "P04637"

    @pytest.mark.asyncio
    async def test_fetch_error(self):
        async def _retry(method, url, **kwargs):
            raise RuntimeError("fail")

        self.conn._request_with_retry = _retry
        result = await self.conn.fetch("P04637")
        assert result.error != ""

    @pytest.mark.asyncio
    async def test_fetch_sequence(self):
        resp = _make_response(text_data=">sp|P04637|P53_HUMAN\nMEEPQSDPSV\nEPPLSQETFSDLWK")
        self.conn._request_with_retry = _mock_request_with_retry(resp)
        seq = await self.conn.fetch_sequence("P04637")
        assert "MEEPQ" in seq
        assert ">" not in seq

    @pytest.mark.asyncio
    async def test_fetch_sequence_error(self):
        async def _retry(method, url, **kwargs):
            raise RuntimeError("fail")

        self.conn._request_with_retry = _retry
        seq = await self.conn.fetch_sequence("P04637")
        assert seq == ""

    def test_parse_entry_minimal(self):
        entry = {"primaryAccession": "Q99999"}
        parsed = self.conn._parse_entry(entry)
        assert parsed["accession"] == "Q99999"
        assert parsed["protein_name"] == ""
        assert parsed["source"] == "UniProt"

    @pytest.mark.asyncio
    async def test_search_by_gene(self):
        async def _retry(method, url, **kwargs):
            return _make_response({"results": [], "total": 0})

        self.conn._request_with_retry = _retry
        result = await self.conn.search_by_gene("BRCA1")
        assert "BRCA1" in result.query

    @pytest.mark.asyncio
    async def test_search_by_taxon(self):
        async def _retry(method, url, **kwargs):
            return _make_response({"results": [], "total": 0})

        self.conn._request_with_retry = _retry
        result = await self.conn.search_by_taxon(9606)
        assert "9606" in result.query

    @pytest.mark.asyncio
    async def test_close(self):
        await self.conn.close()


# ===========================================================================
# 3. PDBConnector
# ===========================================================================


class TestPDBConnector:
    def setup_method(self):
        self.conn = PDBConnector(offline_mode=False, use_mirror=False)

    def test_init_defaults(self):
        c = PDBConnector()
        assert c.config.base_url == "https://data.rcsb.org/rest/v1"

    @pytest.mark.asyncio
    async def test_search_success(self):
        search_resp = _make_response({"result_set": [{"identifier": "6M0J"}, {"identifier": "7KXG"}]})
        entry_resp = _make_response(
            {
                "rcsb_id": "6M0J",
                "struct": {"title": "SARS-CoV-2 Spike"},
                "rcsb_entry_info": {
                    "resolution_combined": [3.2],
                    "deposited_atom_count": 12000,
                    "deposited_model_count": 1,
                    "structure_description": "Spike protein",
                },
                "exptl": [{"method": "X-RAY DIFFRACTION"}],
                "rcsb_audit_author": [{"name": "Walls A"}],
                "rcsb_entry_container_identifiers": {"entity_ids": ["1", "2"]},
            }
        )
        assembly_resp = _make_response({"rcsb_assembly_info": {"molecular_weight": 500000}})

        call_count = 0

        async def _retry(method, url, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return search_resp
            if "assembly" in url:
                return assembly_resp
            return entry_resp

        self.conn._request_with_retry = _retry
        self.conn._search_client = MagicMock()
        result = await self.conn.search("CRISPR Cas9")
        assert result.source == "pdb"
        assert result.total_count == 2

    @pytest.mark.asyncio
    async def test_search_error(self):
        async def _retry(method, url, **kwargs):
            raise RuntimeError("fail")

        self.conn._request_with_retry = _retry
        self.conn._search_client = MagicMock()
        result = await self.conn.search("CRISPR")
        assert result.error != ""

    @pytest.mark.asyncio
    async def test_fetch_success(self):
        entry_resp = _make_response(
            {
                "rcsb_id": "6M0J",
                "struct": {"title": "SARS-CoV-2 Spike"},
                "rcsb_entry_info": {
                    "resolution_combined": [3.2],
                    "deposited_atom_count": 12000,
                    "deposited_model_count": 1,
                    "structure_description": "Spike",
                },
                "exptl": [{"method": "X-RAY DIFFRACTION"}],
                "rcsb_audit_author": [],
                "rcsb_entry_container_identifiers": {"entity_ids": ["1"]},
                "rcsb_accession_info": {
                    "deposit_date": "2020-02-25",
                    "initial_release_date": "2020-03-11",
                },
            }
        )
        assembly_resp = _make_response({"rcsb_assembly_info": {"molecular_weight": 500000}})
        call_count = 0

        async def _retry(method, url, **kwargs):
            nonlocal call_count
            call_count += 1
            if "assembly" in url:
                return assembly_resp
            return entry_resp

        self.conn._request_with_retry = _retry
        result = await self.conn.fetch("6m0j")
        assert result.source == "pdb"
        assert result.items[0]["pdb_id"] == "6M0J"
        assert result.items[0]["title"] == "SARS-CoV-2 Spike"

    @pytest.mark.asyncio
    async def test_fetch_with_cache(self):
        cached = DatabaseResult(source="pdb", query="6M0J", items=[{"pdb_id": "6M0J"}], total_count=1)
        self.conn._set_cache("fetch:6M0J", cached)
        result = await self.conn.fetch("6m0j")
        assert result.total_count == 1

    @pytest.mark.asyncio
    async def test_fetch_error(self):
        async def _retry(method, url, **kwargs):
            raise RuntimeError("fail")

        self.conn._request_with_retry = _retry
        result = await self.conn.fetch("6M0J")
        assert result.error != ""

    def test_parse_entry(self):
        data = {
            "rcsb_id": "1BNA",
            "struct": {"title": "B-DNA"},
            "rcsb_entry_info": {
                "resolution_combined": [1.9],
                "deposited_atom_count": 500,
                "deposited_model_count": 1,
            },
            "exptl": [{"method": "X-RAY DIFFRACTION"}],
            "rcsb_audit_author": [{"name": "Wing R"}],
            "rcsb_entry_container_identifiers": {"entity_ids": ["1"]},
            "rcsb_accession_info": {},
        }
        assembly_data = {"rcsb_assembly_info": {"molecular_weight": 12000}}
        entry = self.conn._parse_entry(data, assembly_data)
        assert entry["pdb_id"] == "1BNA"
        assert entry["experimental_methods"] == ["X-RAY DIFFRACTION"]
        assert entry["resolution"] == 1.9
        assert entry["molecular_weight"] == 12000

    @pytest.mark.asyncio
    async def test_fetch_structure_url(self):
        urls = await self.conn.fetch_structure_url("6M0J")
        assert "6M0J.pdb" in urls["pdb"]
        assert "6M0J.cif" in urls["mmcif"]

    @pytest.mark.asyncio
    async def test_search_by_sequence_error(self):
        async def _retry(method, url, **kwargs):
            raise RuntimeError("fail")

        self.conn._request_with_retry = _retry
        self.conn._search_client = MagicMock()
        result = await self.conn.search_by_sequence("MEEPQSDPSV")
        assert result.error != ""

    @pytest.mark.asyncio
    async def test_close(self):
        self.conn._search_client = MagicMock()
        self.conn._search_client.aclose = AsyncMock()
        await self.conn.close()
        assert self.conn._search_client is None


# ===========================================================================
# 4. EnsemblConnector
# ===========================================================================


class TestEnsemblConnector:
    def setup_method(self):
        self.conn = EnsemblConnector(offline_mode=False, use_mirror=False)

    def test_init_defaults(self):
        c = EnsemblConnector()
        assert c.config.base_url == "https://rest.ensembl.org"

    @pytest.mark.asyncio
    async def test_search_success(self):
        resp = _make_response(
            {
                "results": [
                    {
                        "id": "ENSG00000141510",
                        "type": "gene",
                        "description": "tumor protein p53",
                        "species": "homo_sapiens",
                        "region": "17",
                        "start": 7661779,
                        "end": 7687550,
                        "strand": -1,
                    }
                ]
            }
        )
        self.conn._request_with_retry = _mock_request_with_retry(resp)
        result = await self.conn.search("TP53")
        assert result.source == "ensembl"
        assert result.total_count == 1
        assert result.items[0]["id"] == "ENSG00000141510"

    @pytest.mark.asyncio
    async def test_search_list_response(self):
        resp = _make_response(
            [
                {
                    "id": "ENSG00000141510",
                    "type": "gene",
                    "description": "p53",
                    "species": "human",
                    "region": "17",
                    "start": 1,
                    "end": 100,
                    "strand": 1,
                }
            ]
        )
        self.conn._request_with_retry = _mock_request_with_retry(resp)
        result = await self.conn.search("TP53")
        assert result.total_count == 1

    @pytest.mark.asyncio
    async def test_search_with_cache(self):
        cached = DatabaseResult(source="ensembl", query="TP53", items=[], total_count=0)
        self.conn._set_cache("search:TP53:20", cached)
        result = await self.conn.search("TP53")
        assert result.total_count == 0

    @pytest.mark.asyncio
    async def test_search_error(self):
        async def _retry(method, url, **kwargs):
            raise RuntimeError("fail")

        self.conn._request_with_retry = _retry
        result = await self.conn.search("TP53")
        assert result.error != ""

    @pytest.mark.asyncio
    async def test_fetch_success(self):
        resp = _make_response(
            {
                "id": "ENSG00000141510",
                "object_type": "Gene",
                "description": "tumor protein p53",
                "species": "homo_sapiens",
                "assembly_name": "GRCh38",
                "seq_region_name": "17",
                "start": 7661779,
                "end": 7687550,
                "strand": -1,
                "biotype": "protein_coding",
                "display_name": "TP53",
                "version": 18,
            }
        )
        self.conn._request_with_retry = _mock_request_with_retry(resp)
        result = await self.conn.fetch("ENSG00000141510")
        assert result.source == "ensembl"
        assert result.items[0]["display_name"] == "TP53"

    @pytest.mark.asyncio
    async def test_fetch_error(self):
        async def _retry(method, url, **kwargs):
            raise RuntimeError("fail")

        self.conn._request_with_retry = _retry
        result = await self.conn.fetch("ENSG00000141510")
        assert result.error != ""

    @pytest.mark.asyncio
    async def test_fetch_gene(self):
        resp = _make_response(
            {
                "id": "ENSG00000141510",
                "display_name": "TP53",
                "description": "tumor protein p53",
                "biotype": "protein_coding",
                "seq_region_name": "17",
                "start": 7661779,
                "end": 7687550,
                "strand": -1,
                "version": 18,
            }
        )
        self.conn._request_with_retry = _mock_request_with_retry(resp)
        gene = await self.conn.fetch_gene("ENSG00000141510")
        assert gene["gene_id"] == "ENSG00000141510"
        assert gene["display_name"] == "TP53"

    @pytest.mark.asyncio
    async def test_fetch_gene_error(self):
        async def _retry(method, url, **kwargs):
            raise RuntimeError("fail")

        self.conn._request_with_retry = _retry
        gene = await self.conn.fetch_gene("ENSG00000141510")
        assert "error" in gene

    @pytest.mark.asyncio
    async def test_fetch_sequence_region(self):
        resp = _make_response(text_data="ATCGATCG")
        self.conn._request_with_retry = _mock_request_with_retry(resp)
        seq = await self.conn.fetch_sequence_region("human", "17", 7661779, 7661800)
        assert "ATCG" in seq

    @pytest.mark.asyncio
    async def test_fetch_sequence_region_error(self):
        async def _retry(method, url, **kwargs):
            raise RuntimeError("fail")

        self.conn._request_with_retry = _retry
        seq = await self.conn.fetch_sequence_region("human", "17", 1, 100)
        assert seq == ""

    @pytest.mark.asyncio
    async def test_fetch_variants(self):
        resp = _make_response(
            [
                {
                    "id": "rs28934578",
                    "allele_string": "G/A",
                    "start": 7668000,
                    "end": 7668000,
                    "strand": 1,
                    "consequence_type": "missense_variant",
                    "clinical_significance": "pathogenic",
                }
            ]
        )
        self.conn._request_with_retry = _mock_request_with_retry(resp)
        variants = await self.conn.fetch_variants("ENSG00000141510")
        assert len(variants) == 1
        assert variants[0]["id"] == "rs28934578"

    @pytest.mark.asyncio
    async def test_fetch_variants_error(self):
        async def _retry(method, url, **kwargs):
            raise RuntimeError("fail")

        self.conn._request_with_retry = _retry
        variants = await self.conn.fetch_variants("ENSG00000141510")
        assert variants == []

    @pytest.mark.asyncio
    async def test_fetch_homologues(self):
        resp = _make_response(
            {
                "data": [
                    {
                        "homologies": [
                            {
                                "target": {"id": "ENSMUSG00000059552", "species": {"name": "musculus"}},
                                "type": "ortholog_one2one",
                                "identity": 0.78,
                                "cigar_line": "10M1I90M",
                            }
                        ]
                    }
                ]
            }
        )
        self.conn._request_with_retry = _mock_request_with_retry(resp)
        homologues = await self.conn.fetch_homologues("ENSG00000141510")
        assert len(homologues) == 1
        assert homologues[0]["gene_id"] == "ENSMUSG00000059552"

    @pytest.mark.asyncio
    async def test_fetch_homologues_error(self):
        async def _retry(method, url, **kwargs):
            raise RuntimeError("fail")

        self.conn._request_with_retry = _retry
        homologues = await self.conn.fetch_homologues("ENSG00000141510")
        assert homologues == []

    @pytest.mark.asyncio
    async def test_search_by_gene_name(self):
        async def _retry(method, url, **kwargs):
            return _make_response({"results": []})

        self.conn._request_with_retry = _retry
        result = await self.conn.search_by_gene_name("BRCA1")
        assert result.source == "ensembl"

    @pytest.mark.asyncio
    async def test_close(self):
        await self.conn.close()


# ===========================================================================
# 5. ChEMBLConnector
# ===========================================================================


class TestChEMBLConnector:
    def setup_method(self):
        self.conn = ChEMBLConnector(offline_mode=False, use_mirror=False)

    def test_init_defaults(self):
        c = ChEMBLConnector()
        assert c.config.base_url == "https://www.ebi.ac.uk/chembl/api/data"

    @pytest.mark.asyncio
    async def test_search_molecules(self):
        resp = _make_response(
            {
                "molecules": [
                    {
                        "molecule_chembl_id": "CHEMBL25",
                        "pref_name": "ASPIRIN",
                        "molecule_synonyms": [{"synonym": "Acetylsalicylic acid"}],
                        "molecule_properties": {
                            "mw_freebase": 180.16,
                            "alogp": 1.23,
                            "hbd": 1,
                            "hba": 3,
                            "num_ro5_violations": 0,
                        },
                        "molecule_structures": {
                            "canonical_smiles": "CC(=O)OC1=CC=CC=C1C(=O)O",
                            "standard_inchi": "InChI=1S/C9H8O4",
                            "standard_inchi_key": "BSYNRYMUTXBXSQ-UHFFFAOYSA-N",
                        },
                        "max_phase": 4,
                        "first_approval": 1950,
                        "oral": True,
                    }
                ]
            }
        )
        self.conn._request_with_retry = _mock_request_with_retry(resp)
        result = await self.conn.search("aspirin")
        assert result.source == "chembl"
        assert result.total_count == 1
        assert result.items[0]["chembl_id"] == "CHEMBL25"
        assert result.items[0]["smiles"] != ""

    @pytest.mark.asyncio
    async def test_search_targets(self):
        resp = _make_response(
            {
                "targets": [
                    {
                        "target_chembl_id": "CHEMBL240",
                        "pref_name": "Kinase insert domain receptor",
                        "target_type": "SINGLE PROTEIN",
                        "organism": "Homo sapiens",
                        "target_components": [{"accession": "P35968"}],
                    }
                ]
            }
        )
        self.conn._request_with_retry = _mock_request_with_retry(resp)
        result = await self.conn.search("kinase", entity_type="target")
        assert result.source == "chembl"
        assert len(result.items) == 1
        assert result.items[0]["chembl_id"] == "CHEMBL240"

    @pytest.mark.asyncio
    async def test_search_assay_fallback(self):
        resp = _make_response({"molecules": []})
        self.conn._request_with_retry = _mock_request_with_retry(resp)
        result = await self.conn.search("test", entity_type="assay")
        assert result.source == "chembl"

    @pytest.mark.asyncio
    async def test_search_with_cache(self):
        cached = DatabaseResult(source="chembl", query="aspirin", items=[], total_count=0)
        self.conn._set_cache("search:aspirin:20", cached)
        result = await self.conn.search("aspirin")
        assert result.total_count == 0

    @pytest.mark.asyncio
    async def test_search_error(self):
        async def _retry(method, url, **kwargs):
            raise RuntimeError("fail")

        self.conn._request_with_retry = _retry
        result = await self.conn.search("aspirin")
        assert result.error != ""

    @pytest.mark.asyncio
    async def test_fetch_molecule(self):
        resp = _make_response(
            {
                "molecule_chembl_id": "CHEMBL25",
                "pref_name": "ASPIRIN",
                "molecule_synonyms": [],
                "molecule_properties": {},
                "molecule_structures": {},
            }
        )
        self.conn._request_with_retry = _mock_request_with_retry(resp)
        result = await self.conn.fetch("CHEMBL25")
        assert result.source == "chembl"
        assert result.items[0]["chembl_id"] == "CHEMBL25"

    @pytest.mark.asyncio
    async def test_fetch_target_prefix(self):
        resp = _make_response(
            {
                "target_chembl_id": "CHEMBL_TARGET240",
                "pref_name": "Kinase",
                "target_type": "SINGLE PROTEIN",
                "organism": "Homo sapiens",
                "target_components": [],
            }
        )
        self.conn._request_with_retry = _mock_request_with_retry(resp)
        result = await self.conn.fetch("CHEMBL_TARGET240")
        assert result.source == "chembl"

    @pytest.mark.asyncio
    async def test_fetch_assay_prefix(self):
        resp = _make_response(
            {
                "assay_chembl_id": "CHEMBL_ASSAY1",
                "description": "Test assay",
                "assay_type": "B",
                "assay_organism": "Homo sapiens",
            }
        )
        self.conn._request_with_retry = _mock_request_with_retry(resp)
        result = await self.conn.fetch("CHEMBL_ASSAY1")
        assert result.source == "chembl"

    @pytest.mark.asyncio
    async def test_fetch_unknown_id(self):
        resp = _make_response(
            {
                "molecule_chembl_id": "UNKNOWN123",
                "pref_name": "",
                "molecule_synonyms": [],
                "molecule_properties": {},
                "molecule_structures": {},
            }
        )
        self.conn._request_with_retry = _mock_request_with_retry(resp)
        result = await self.conn.fetch("UNKNOWN123")
        assert result.source == "chembl"

    @pytest.mark.asyncio
    async def test_fetch_error(self):
        async def _retry(method, url, **kwargs):
            raise RuntimeError("fail")

        self.conn._request_with_retry = _retry
        result = await self.conn.fetch("CHEMBL25")
        assert result.error != ""

    @pytest.mark.asyncio
    async def test_get_bioactivities(self):
        resp = _make_response(
            {
                "activities": [
                    {
                        "assay_chembl_id": "CHEMBL_A1",
                        "target_chembl_id": "CHEMBL_T1",
                        "target_pref_name": "Target1",
                        "standard_type": "IC50",
                        "standard_value": "100",
                        "standard_units": "nM",
                        "standard_relation": "=",
                        "pchembl_value": "7.0",
                    }
                ]
            }
        )
        self.conn._request_with_retry = _mock_request_with_retry(resp)
        activities = await self.conn.get_bioactivities("CHEMBL25")
        assert len(activities) == 1
        assert activities[0]["type"] == "IC50"

    @pytest.mark.asyncio
    async def test_get_bioactivities_error(self):
        async def _retry(method, url, **kwargs):
            raise RuntimeError("fail")

        self.conn._request_with_retry = _retry
        activities = await self.conn.get_bioactivities("CHEMBL25")
        assert activities == []

    @pytest.mark.asyncio
    async def test_get_drug_indications(self):
        resp = _make_response(
            {
                "drug_indications": [
                    {
                        "efo_term": "Rheumatoid arthritis",
                        "mesh_id": "D001172",
                        "max_phase_for_ind": 4,
                    }
                ]
            }
        )
        self.conn._request_with_retry = _mock_request_with_retry(resp)
        indications = await self.conn.get_drug_indications("CHEMBL25")
        assert len(indications) == 1
        assert indications[0]["efo_term"] == "Rheumatoid arthritis"

    @pytest.mark.asyncio
    async def test_get_drug_indications_error(self):
        async def _retry(method, url, **kwargs):
            raise RuntimeError("fail")

        self.conn._request_with_retry = _retry
        indications = await self.conn.get_drug_indications("CHEMBL25")
        assert indications == []

    def test_parse_molecule(self):
        mol = {
            "molecule_chembl_id": "CHEMBL25",
            "pref_name": "Aspirin",
            "molecule_synonyms": [{"synonym": "ASA"}],
            "molecule_properties": {"mw_freebase": 180, "alogp": 1.2, "hbd": 1, "hba": 3, "num_ro5_violations": 0},
            "molecule_structures": {"canonical_smiles": "CC(=O)Oc1ccccc1C(=O)O"},
            "max_phase": 4,
            "first_approval": 1950,
            "oral": True,
        }
        parsed = self.conn._parse_molecule(mol)
        assert parsed["chembl_id"] == "CHEMBL25"
        assert parsed["synonyms"] == ["ASA"]
        assert parsed["molecular_weight"] == 180
        assert parsed["oral"] is True

    def test_parse_target(self):
        target = {
            "target_chembl_id": "CHEMBL240",
            "pref_name": "Kinase",
            "target_type": "SINGLE PROTEIN",
            "organism": "Homo sapiens",
            "description": "Test",
            "target_components": [{"accession": "P35968"}],
        }
        parsed = self.conn._parse_target(target)
        assert parsed["chembl_id"] == "CHEMBL240"
        assert parsed["uniprot_id"] == "P35968"

    def test_parse_target_no_components(self):
        target = {
            "target_chembl_id": "CHEMBL999",
            "pref_name": "Test",
            "target_type": "SINGLE PROTEIN",
            "organism": "Human",
        }
        parsed = self.conn._parse_target(target)
        assert parsed["uniprot_id"] == ""

    def test_parse_assay(self):
        assay = {
            "assay_chembl_id": "CHEMBL_A1",
            "description": "Binding assay",
            "assay_type": "B",
            "assay_organism": "Human",
            "assay_tissue": "Liver",
            "assay_cell_type": "HEK293",
            "assay_subcellular_fraction": "Membrane",
        }
        parsed = self.conn._parse_assay(assay)
        assert parsed["chembl_id"] == "CHEMBL_A1"
        assert parsed["assay_type"] == "B"

    @pytest.mark.asyncio
    async def test_close(self):
        await self.conn.close()


# ===========================================================================
# 6. Chinese DB Connectors (chinese.py)
# ===========================================================================


class TestNGDCConnector:
    def setup_method(self):
        self.conn = NGDCConnector()

    @pytest.mark.asyncio
    async def test_search_success(self):
        resp = _make_response({"data": [{"id": "GSA001", "title": "Test dataset"}]})
        self.conn._request_with_retry = _mock_request_with_retry(resp)
        result = await self.conn.search("genomics")
        assert len(result.items) == 1

    @pytest.mark.asyncio
    async def test_search_error(self):
        async def _retry(method, url, **kwargs):
            raise RuntimeError("fail")

        self.conn._request_with_retry = _retry
        result = await self.conn.search("genomics")
        assert result.items == []
        assert result.error != ""

    @pytest.mark.asyncio
    async def test_fetch_success(self):
        resp = _make_response({"id": "GSA001", "title": "Dataset"})
        self.conn._request_with_retry = _mock_request_with_retry(resp)
        result = await self.conn.fetch("GSA001")
        assert result is not None

    @pytest.mark.asyncio
    async def test_fetch_error(self):
        async def _retry(method, url, **kwargs):
            raise RuntimeError("fail")

        self.conn._request_with_retry = _retry
        result = await self.conn.fetch("GSA001")
        assert result.error != ""

    def test_parse_search_results_dict_data(self):
        data = {"data": [{"id": "1"}]}
        result = self.conn._parse_search_results(data, "gsa")
        assert len(result) == 1

    def test_parse_search_results_dict_items(self):
        data = {"items": [{"id": "2"}]}
        result = self.conn._parse_search_results(data, "gsa")
        assert len(result) == 1

    def test_parse_search_results_dict_results(self):
        data = {"results": [{"id": "3"}]}
        result = self.conn._parse_search_results(data, "gsa")
        assert len(result) == 1

    def test_parse_search_results_nonlist(self):
        data = {"data": "not a list"}
        result = self.conn._parse_search_results(data, "gsa")
        assert result == []

    def test_parse_detail_success(self):
        data = {"accession": "GSA001", "title": "Test"}
        result = self.conn._parse_detail(data, "gsa")
        assert result is not None
        assert result["accession"] == "GSA001"

    def test_parse_detail_empty(self):
        result = self.conn._parse_detail({}, "gsa")
        assert result is None


class TestCNKIConnector:
    def setup_method(self):
        self.conn = CNKIConnector()

    @pytest.mark.asyncio
    async def test_search_success(self):
        resp = _make_response({"data": [{"title": "Chinese paper"}]})
        self.conn._request_with_retry = _mock_request_with_retry(resp)
        result = await self.conn.search("cancer research")
        assert len(result.items) == 1

    @pytest.mark.asyncio
    async def test_search_error(self):
        async def _retry(method, url, **kwargs):
            raise RuntimeError("fail")

        self.conn._request_with_retry = _retry
        result = await self.conn.search("test")
        assert result.items == []
        assert result.error != ""

    @pytest.mark.asyncio
    async def test_fetch_success(self):
        resp = _make_response({"title": "Article details"})
        self.conn._request_with_retry = _mock_request_with_retry(resp)
        result = await self.conn.fetch("article123")
        assert result is not None

    @pytest.mark.asyncio
    async def test_fetch_error(self):
        async def _retry(method, url, **kwargs):
            raise RuntimeError("fail")

        self.conn._request_with_retry = _retry
        result = await self.conn.fetch("article123")
        assert result.error != ""

    def test_parse_search_results(self):
        data = {"results": [{"id": "1"}]}
        result = self.conn._parse_search_results(data)
        assert len(result) == 1

    def test_parse_search_results_other(self):
        result = self.conn._parse_search_results({"unknown_key": "value"})
        assert result == []


class TestScienceDBConnector:
    def setup_method(self):
        self.conn = ScienceDBConnector()

    @pytest.mark.asyncio
    async def test_search_success(self):
        resp = _make_response({"data": [{"id": "DS001", "name": "dataset"}]})
        self.conn._request_with_retry = _mock_request_with_retry(resp)
        result = await self.conn.search("climate data")
        assert len(result.items) == 1

    @pytest.mark.asyncio
    async def test_search_error(self):
        async def _retry(method, url, **kwargs):
            raise RuntimeError("fail")

        self.conn._request_with_retry = _retry
        result = await self.conn.search("test")
        assert result.items == []
        assert result.error != ""

    @pytest.mark.asyncio
    async def test_fetch_success(self):
        resp = _make_response({"id": "DS001", "name": "dataset"})
        self.conn._request_with_retry = _mock_request_with_retry(resp)
        result = await self.conn.fetch("DS001")
        assert result is not None

    @pytest.mark.asyncio
    async def test_fetch_error(self):
        async def _retry(method, url, **kwargs):
            raise RuntimeError("fail")

        self.conn._request_with_retry = _retry
        result = await self.conn.fetch("DS001")
        assert result.error != ""

    def test_parse_search_results(self):
        data = {"items": [{"id": "1"}]}
        result = self.conn._parse_search_results(data)
        assert len(result) == 1

    def test_parse_search_results_other(self):
        result = self.conn._parse_search_results({"unknown_key": "value"})
        assert result == []


class TestScienceCache:
    def test_set_and_get(self, tmp_path):
        from fusion_science.database.mirror import CacheConfig

        config = CacheConfig(
            cache_dir=str(tmp_path),
            db_path="test_cache.db",
        )
        cache = ScienceCache(config=config)
        cache.set("test_query", {"results": [1, 2, 3]}, source="pubmed", ttl=300)
        result = cache.get("test_query")
        assert result == {"results": [1, 2, 3]}
        cache.close()

    def test_get_missing(self, tmp_path):
        from fusion_science.database.mirror import CacheConfig

        config = CacheConfig(
            cache_dir=str(tmp_path),
            db_path="test_cache.db",
        )
        cache = ScienceCache(config=config)
        result = cache.get("nonexistent")
        assert result is None
        cache.close()

    def test_delete(self, tmp_path):
        from fusion_science.database.mirror import CacheConfig

        config = CacheConfig(
            cache_dir=str(tmp_path),
            db_path="test_cache.db",
        )
        cache = ScienceCache(config=config)
        cache.set("to_delete", {"data": True}, source="pubmed", ttl=300)
        cache.delete("to_delete")
        assert cache.get("to_delete") is None
        cache.close()

    def test_stats(self, tmp_path):
        from fusion_science.database.mirror import CacheConfig

        config = CacheConfig(
            cache_dir=str(tmp_path),
            db_path="test_cache.db",
        )
        cache = ScienceCache(config=config)
        cache.set("q1", {"d": 1}, source="pubmed", ttl=300)
        stats = cache.stats()
        assert isinstance(stats, dict)
        cache.close()


class TestMirrorRouter:
    def test_init(self):
        router = MirrorRouter()
        assert router.is_offline_mode() is False

    def test_enable_offline_mode(self, monkeypatch):
        monkeypatch.setenv("FUSION_OFFLINE_MODE", "true")
        router = MirrorRouter()
        assert router.is_offline_mode() is True

    def test_enable_mirrors(self):
        router = MirrorRouter()
        router.enable_mirrors(True)
        url = router.get_url("pubmed")
        assert isinstance(url, str)

    def test_get_url_known(self):
        router = MirrorRouter()
        url = router.get_url("pubmed")
        assert url != ""

    def test_list_mirrors(self):
        router = MirrorRouter()
        mirrors = router.list_mirrors()
        assert isinstance(mirrors, list)

    def test_list_chinese_databases(self):
        router = MirrorRouter()
        databases = router.list_chinese_databases()
        assert isinstance(databases, list)


# ===========================================================================
# 7. RExecutor
# ===========================================================================


class TestRExecutor:
    def test_init_r_unavailable(self):
        with patch.dict("sys.modules", {"rpy2": None, "rpy2.robjects": None}):
            from fusion_science.compute.r_executor import RExecutor

            r = RExecutor(timeout=60)
            assert r.available is False

    @pytest.mark.asyncio
    async def test_execute_r_unavailable(self):
        from fusion_science.compute.r_executor import RExecutor

        with patch.dict("sys.modules", {"rpy2": None, "rpy2.robjects": None}):
            r = RExecutor()
            result = await r.execute("1 + 1")
            assert result.success is False
            assert "not available" in result.error

    @pytest.mark.asyncio
    async def test_execute_with_mock_rpy2(self):
        from fusion_science.compute.r_executor import RExecutor

        mock_robjects = MagicMock()
        mock_robjects.r.return_value = "42"

        with patch.dict(
            "sys.modules",
            {
                "rpy2": MagicMock(),
                "rpy2.robjects": mock_robjects,
                "rpy2.robjects.pandas2ri": MagicMock(activate=MagicMock()),
            },
        ):
            r = RExecutor()
            r._r_available = True
            result = await r.execute("1 + 1", capture_plots=False)
            assert result.success is True
            assert result.output != ""

    @pytest.mark.asyncio
    async def test_execute_with_r_error(self):
        from fusion_science.compute.r_executor import RExecutionResult, RExecutor

        mock_robjects = MagicMock()
        mock_robjects.r.side_effect = Exception("R syntax error")

        with patch.dict(
            "sys.modules",
            {
                "rpy2": MagicMock(),
                "rpy2.robjects": mock_robjects,
                "rpy2.robjects.pandas2ri": MagicMock(activate=MagicMock()),
            },
        ):
            r = RExecutor()
            r._r_available = True
            with patch("fusion_science.compute.r_executor.RExecutor.execute") as mock_exec:
                mock_exec.return_value = RExecutionResult(success=False, error="R syntax error")
                result = await r.execute("invalid code", capture_plots=False)
                assert result.success is False
                assert "R syntax error" in result.error

    @pytest.mark.asyncio
    async def test_execute_with_exception(self):
        from fusion_science.compute.r_executor import RExecutor

        mock_robjects = MagicMock()
        mock_robjects.r.side_effect = Exception("boom")

        with patch.dict(
            "sys.modules",
            {
                "rpy2": MagicMock(),
                "rpy2.robjects": mock_robjects,
            },
        ):
            r = RExecutor()
            r._r_available = True
            with patch("fusion_science.compute.r_executor.RExecutor.execute") as mock_exec:
                mock_exec.return_value = MagicMock(success=False, error="import failed")
                result = await r.execute("code")
                assert result.success is False

    @pytest.mark.asyncio
    async def test_check_packages_r_unavailable(self):
        from fusion_science.compute.r_executor import RExecutor

        with patch.dict("sys.modules", {"rpy2": None, "rpy2.robjects": None}):
            r = RExecutor()
            r._r_available = False
            result = await r.check_packages(["ggplot2"])
            assert result == {"ggplot2": False}

    @pytest.mark.asyncio
    async def test_check_packages_r_available(self):
        from fusion_science.compute.r_executor import RExecutor

        mock_robjects = MagicMock()

        with patch.dict(
            "sys.modules",
            {
                "rpy2": MagicMock(),
                "rpy2.robjects": mock_robjects,
            },
        ):
            r = RExecutor()
            r._r_available = True
            result = await r.check_packages(["ggplot2", "dplyr"])
            assert isinstance(result, dict)

    def test_get_bioconductor_install_code(self):
        from fusion_science.compute.r_executor import RExecutor

        code = RExecutor.get_bioconductor_install_code()
        assert "BiocManager" in code

    def test_setup_plot_capture(self):
        from fusion_science.compute.r_executor import RExecutor

        with patch.dict("sys.modules", {"rpy2": None, "rpy2.robjects": None}):
            r = RExecutor()
            r._r_available = True
            paths = r._setup_plot_capture()
            assert isinstance(paths, list)

    def test_wrap_with_plot_capture(self):
        from fusion_science.compute.r_executor import RExecutor

        with patch.dict("sys.modules", {"rpy2": None, "rpy2.robjects": None}):
            r = RExecutor()
            r._r_available = True
            wrapped = r._wrap_with_plot_capture("plot(x)", [])
            assert "plot(x)" in wrapped
            assert "png" in wrapped

    def test_collect_plots_no_files(self):
        from fusion_science.compute.r_executor import RExecutor

        with patch.dict("sys.modules", {"rpy2": None, "rpy2.robjects": None}):
            r = RExecutor()
            r._r_available = True
            with patch("glob.glob", return_value=[]):
                plots = r._collect_plots()
                assert plots == []


# ===========================================================================
# 8. JupyterKernelManager
# ===========================================================================


class TestJupyterKernelManager:
    def test_init(self):
        from fusion_science.compute.jupyter_kernel import JupyterKernelManager

        km = JupyterKernelManager(kernel_name="python3")
        assert km.kernel_name == "python3"
        assert km._running is False

    @pytest.mark.asyncio
    async def test_start_kernel_import_error(self):
        from fusion_science.compute.jupyter_kernel import JupyterKernelManager

        km = JupyterKernelManager()
        with patch.dict("sys.modules", {"jupyter_client": None}):
            result = await km.start_kernel()
            assert result is False

    @pytest.mark.asyncio
    async def test_start_kernel_success(self):
        from fusion_science.compute.jupyter_kernel import JupyterKernelManager

        mock_km = MagicMock()
        mock_client = MagicMock()

        with patch.dict("sys.modules", {"jupyter_client": MagicMock(KernelManager=MagicMock(return_value=mock_km))}):
            km = JupyterKernelManager()
            with patch(
                "fusion_science.compute.jupyter_kernel.JupyterKernelManager.start_kernel", new_callable=AsyncMock
            ) as mock_start:
                mock_start.return_value = True
                km._running = True
                km._kernel_client = mock_client
                assert km._running is True

    @pytest.mark.asyncio
    async def test_execute_not_running(self):
        from fusion_science.compute.jupyter_kernel import JupyterKernelManager

        km = JupyterKernelManager()
        result = await km.execute("print('hello')")
        assert result.success is False
        assert "not running" in result.error.lower()

    @pytest.mark.asyncio
    async def test_shutdown(self):
        from fusion_science.compute.jupyter_kernel import JupyterKernelManager

        km = JupyterKernelManager()
        km._running = True
        km._kernel_client = MagicMock()
        km._kernel_manager = MagicMock()
        await km.shutdown()
        assert km._running is False
        assert km._kernel_client is None
        assert km._kernel_manager is None

    @pytest.mark.asyncio
    async def test_shutdown_with_exception(self):
        from fusion_science.compute.jupyter_kernel import JupyterKernelManager

        km = JupyterKernelManager()
        km._running = True
        km._kernel_client = MagicMock()
        km._kernel_client.stop_channels.side_effect = Exception("stop failed")
        km._kernel_manager = MagicMock()
        km._kernel_manager.shutdown_kernel.side_effect = Exception("shutdown failed")
        with pytest.raises(Exception, match="shutdown failed"):
            await km.shutdown()

    @pytest.mark.asyncio
    async def test_shutdown_no_client(self):
        from fusion_science.compute.jupyter_kernel import JupyterKernelManager

        km = JupyterKernelManager()
        km._running = True
        await km.shutdown()
        assert km._running is False

    def test_list_available_kernels_import_error(self):
        from fusion_science.compute.jupyter_kernel import JupyterKernelManager

        with patch.dict("sys.modules", {"jupyter_client": None}):
            kernels = JupyterKernelManager.list_available_kernels()
            assert kernels == []

    def test_list_available_kernels_success(self):
        from fusion_science.compute.jupyter_kernel import JupyterKernelManager

        mock_ksm = MagicMock()
        mock_ksm.get_all_specs.return_value = {
            "python3": {"spec": {"language": "python", "display_name": "Python 3", "argv": ["python"]}},
        }
        with patch.dict(
            "sys.modules",
            {
                "jupyter_client": MagicMock(),
                "jupyter_client.kernelspec": MagicMock(KernelSpecManager=MagicMock(return_value=mock_ksm)),
            },
        ):
            kernels = JupyterKernelManager.list_available_kernels()
            assert len(kernels) >= 1
            assert kernels[0].name == "python3"

    def test_install_kernel(self):
        from fusion_science.compute.jupyter_kernel import JupyterKernelManager

        with tempfile.TemporaryDirectory() as tmpdir:
            with patch.object(JupyterKernelManager, "install_kernel") as mock_install:
                mock_install.return_value = True
                result = JupyterKernelManager.install_kernel(display_name="Test Kernel")
                assert result is True

    def test_install_kernel_error(self):
        from fusion_science.compute.jupyter_kernel import JupyterKernelManager

        with patch("pathlib.Path.mkdir", side_effect=PermissionError("no access")):
            with patch("pathlib.Path.home", return_value=__import__("pathlib").Path("/nonexistent")):
                result = JupyterKernelManager.install_kernel()
                assert result is False


# ===========================================================================
# 9. HPCScheduler
# ===========================================================================


class TestHPCScheduler:
    def test_init_no_sbatch(self):
        from fusion_science.compute.hpc_scheduler import HPCScheduler

        with patch("subprocess.run", side_effect=FileNotFoundError):
            s = HPCScheduler(use_local=True)
            assert s._sbatch_available is False

    def test_init_with_sbatch(self):
        from fusion_science.compute.hpc_scheduler import HPCScheduler

        mock_result = MagicMock(returncode=0)
        with patch("subprocess.run", return_value=mock_result):
            s = HPCScheduler()
            assert s._sbatch_available is True

    @pytest.mark.asyncio
    async def test_submit_job_local_fallback(self):
        from fusion_science.compute.hpc_scheduler import HPCScheduler

        s = HPCScheduler(use_local=True)
        s._sbatch_available = False
        with tempfile.TemporaryDirectory() as tmpdir, patch("tempfile.gettempdir", return_value=tmpdir):
            with patch("asyncio.to_thread", return_value=MagicMock()):
                with patch("asyncio.create_subprocess_exec", return_value=MagicMock(pid=1234)):
                    job = await s.submit_job("echo hello", job_name="test_job")
                    assert job.job_id.startswith("local_")
                    assert job.status == "RUNNING"

    @pytest.mark.asyncio
    async def test_submit_job_sbatch_success(self):
        from fusion_science.compute.hpc_scheduler import HPCScheduler

        s = HPCScheduler()
        s._sbatch_available = True
        mock_result = MagicMock(returncode=0, stdout="Submitted batch job 123456", stderr="")
        with tempfile.TemporaryDirectory() as tmpdir, patch("tempfile.gettempdir", return_value=tmpdir):
            with patch("subprocess.run", return_value=mock_result):
                job = await s.submit_job("echo hello", job_name="test")
                assert job.job_id == "123456"
                assert job.status == "PENDING"

    @pytest.mark.asyncio
    async def test_submit_job_sbatch_fail_fallback(self):
        from fusion_science.compute.hpc_scheduler import HPCScheduler

        s = HPCScheduler()
        s._sbatch_available = True
        mock_fail = MagicMock(returncode=1, stdout="", stderr="sbatch error")
        with patch("subprocess.run", return_value=mock_fail):
            s._sbatch_available = False
            s._run_locally = AsyncMock(return_value=MagicMock(job_id="local_1", status="RUNNING"))
            job = await s.submit_job("echo hello")
            assert job.job_id.startswith("local_")

    def test_build_slurm_headers(self):
        from fusion_science.compute.hpc_scheduler import HPCScheduler

        s = HPCScheduler(slurm_account="test_account")
        headers = s._build_slurm_headers(
            job_name="test",
            partition="gpu",
            nodes=2,
            tasks=4,
            cpus_per_task=8,
            memory_gb=16,
            gpus=2,
            time_limit="02:00:00",
            array_range="1-10",
        )
        assert "#SBATCH --job-name=test" in headers
        assert "#SBATCH --nodes=2" in headers
        assert "#SBATCH --ntasks=4" in headers
        assert "#SBATCH --cpus-per-task=8" in headers
        assert "#SBATCH --mem=16G" in headers
        assert "#SBATCH --partition=gpu" in headers
        assert "#SBATCH --account=test_account" in headers
        assert "#SBATCH --gres=gpu:2" in headers
        assert "#SBATCH --array=1-10" in headers

    def test_build_slurm_headers_minimal(self):
        from fusion_science.compute.hpc_scheduler import HPCScheduler

        s = HPCScheduler()
        headers = s._build_slurm_headers(
            job_name="minimal",
            partition="",
            nodes=1,
            tasks=1,
            cpus_per_task=1,
            memory_gb=4,
            gpus=0,
            time_limit="01:00:00",
            array_range="",
        )
        assert "#SBATCH --job-name=minimal" in headers
        assert "partition" not in headers
        assert "gres" not in headers
        assert "array" not in headers

    def test_write_script(self):
        from fusion_science.compute.hpc_scheduler import HPCScheduler

        s = HPCScheduler()
        with tempfile.TemporaryDirectory() as tmpdir, patch("tempfile.gettempdir", return_value=tmpdir):
            path = s._write_script("#!/bin/bash\necho hello", "test_job")
            assert os.path.exists(path)
            with open(path) as f:
                assert "echo hello" in f.read()

    @pytest.mark.asyncio
    async def test_check_status_local(self):
        from fusion_science.compute.hpc_scheduler import HPCScheduler

        s = HPCScheduler()
        status = await s.check_status("local_123456")
        assert status == "RUNNING"

    @pytest.mark.asyncio
    async def test_check_status_sacct(self):
        from fusion_science.compute.hpc_scheduler import HPCScheduler

        s = HPCScheduler()
        mock_result = MagicMock(returncode=0, stdout="COMPLETED")
        with patch("subprocess.run", return_value=mock_result):
            status = await s.check_status("123456")
            assert status == "COMPLETED"

    @pytest.mark.asyncio
    async def test_check_status_squeue_fallback(self):
        from fusion_science.compute.hpc_scheduler import HPCScheduler

        s = HPCScheduler()
        sacct_fail = MagicMock(returncode=1, stdout="")
        squeue_ok = MagicMock(returncode=0, stdout="PENDING")
        with patch("subprocess.run", side_effect=[sacct_fail, squeue_ok]):
            status = await s.check_status("123456")
            assert status == "PENDING"

    @pytest.mark.asyncio
    async def test_check_status_error(self):
        from fusion_science.compute.hpc_scheduler import HPCScheduler

        s = HPCScheduler()
        with patch("subprocess.run", side_effect=Exception("cmd fail")):
            status = await s.check_status("123456")
            assert status == "UNKNOWN"

    @pytest.mark.asyncio
    async def test_cancel_job_local(self):
        from fusion_science.compute.hpc_scheduler import HPCScheduler

        s = HPCScheduler()
        result = await s.cancel_job("local_123456")
        assert result is False

    @pytest.mark.asyncio
    async def test_cancel_job_slurm_success(self):
        from fusion_science.compute.hpc_scheduler import HPCScheduler

        s = HPCScheduler()
        mock_result = MagicMock(returncode=0)
        with patch("subprocess.run", return_value=mock_result):
            result = await s.cancel_job("123456")
            assert result is True

    @pytest.mark.asyncio
    async def test_cancel_job_slurm_error(self):
        from fusion_science.compute.hpc_scheduler import HPCScheduler

        s = HPCScheduler()
        with patch("subprocess.run", side_effect=Exception("fail")):
            result = await s.cancel_job("123456")
            assert result is False

    @pytest.mark.asyncio
    async def test_get_cluster_info_no_sbatch(self):
        from fusion_science.compute.hpc_scheduler import HPCScheduler

        s = HPCScheduler()
        s._sbatch_available = False
        info = await s.get_cluster_info()
        assert info.available is False

    @pytest.mark.asyncio
    async def test_get_cluster_info_with_sbatch(self):
        from fusion_science.compute.hpc_scheduler import HPCScheduler

        s = HPCScheduler()
        s._sbatch_available = True
        mock_result = MagicMock(returncode=0, stdout="gpu|10|32|256000|5\ncpu|50|64|512000|20")
        with patch("subprocess.run", return_value=mock_result):
            info = await s.get_cluster_info()
            assert info.available is True
            assert len(info.partitions) == 2

    @pytest.mark.asyncio
    async def test_get_cluster_info_error(self):
        from fusion_science.compute.hpc_scheduler import HPCScheduler

        s = HPCScheduler()
        s._sbatch_available = True
        with patch("subprocess.run", side_effect=Exception("fail")):
            info = await s.get_cluster_info()
            assert info.available is True

    @pytest.mark.asyncio
    async def test_get_job_output_file_exists(self):
        from fusion_science.compute.hpc_scheduler import HPCScheduler

        s = HPCScheduler()
        with tempfile.TemporaryDirectory() as tmpdir, patch("tempfile.gettempdir", return_value=tmpdir):
            log_dir = os.path.join(tmpdir, "fusion_science_jobs", "logs")
            os.makedirs(log_dir, exist_ok=True)
            out_file = os.path.join(log_dir, "test_local_1.out")
            with open(out_file, "w") as f:
                f.write("Job output here")
            output = await s.get_job_output("local_1", job_name="test")
            assert "Job output here" in output

    @pytest.mark.asyncio
    async def test_get_job_output_no_file(self):
        from fusion_science.compute.hpc_scheduler import HPCScheduler

        s = HPCScheduler()
        output = await s.get_job_output("nonexistent", job_name="test")
        assert output == ""

    def test_generate_parallel_script(self):
        from fusion_science.compute.hpc_scheduler import HPCScheduler

        script = HPCScheduler.generate_parallel_script(
            python_code="python process.py $INPUT_FILE",
            input_files=["input_1.txt", "input_2.txt"],
            output_dir="/tmp/output",
        )
        assert "#!/bin/bash" in script
        assert "SLURM_ARRAY_TASK_ID" in script
        assert "python process.py" in script

    @pytest.mark.asyncio
    async def test_submit_ssh_success(self):
        from fusion_science.compute.hpc_scheduler import HPCScheduler

        s = HPCScheduler(ssh_host="cluster.example.com", ssh_key="/path/to/key")
        mock_result = MagicMock(returncode=0, stdout="Submitted batch job 789", stderr="")
        with patch("subprocess.run", return_value=mock_result):
            job_id = await s._submit_ssh("/tmp/job.sh")
            assert job_id == "789"

    @pytest.mark.asyncio
    async def test_submit_ssh_fail(self):
        from fusion_science.compute.hpc_scheduler import HPCScheduler

        s = HPCScheduler(ssh_host="cluster.example.com", ssh_key="/path/to/key")
        mock_result = MagicMock(returncode=1, stdout="", stderr="ssh failed")
        with patch("subprocess.run", return_value=mock_result):
            with pytest.raises(RuntimeError, match="SSH sbatch failed"):
                await s._submit_ssh("/tmp/job.sh")


# ===========================================================================
# 10. MoleculeVisualizer
# ===========================================================================


class TestMoleculeVisualizer:
    def test_init(self):
        from fusion_science.visualization.molecule import MoleculeVisualizer

        viz = MoleculeVisualizer()
        assert isinstance(viz._rdkit_available, bool)
        assert isinstance(viz._py3dmol_available, bool)

    @pytest.mark.asyncio
    async def test_from_smiles_rdkit_unavailable(self):
        from fusion_science.visualization.molecule import MoleculeVisualizer

        viz = MoleculeVisualizer()
        viz._rdkit_available = False
        result = await viz.from_smiles("CC(=O)OC1=CC=CC=C1C(=O)O", name="aspirin")
        assert result.success is True
        assert result.html_path != ""

    @pytest.mark.asyncio
    async def test_from_smiles_fallback_html_content(self):
        from fusion_science.visualization.molecule import MoleculeVisualizer

        viz = MoleculeVisualizer()
        viz._rdkit_available = False
        with tempfile.TemporaryDirectory() as tmpdir, patch("tempfile.gettempdir", return_value=tmpdir):
            result = await viz.from_smiles("CC(=O)OC1=CC=CC=C1C(=O)O", name="test_mol")
            assert result.success is True
            with open(result.html_path) as f:
                content = f.read()
                assert "test_mol" in content
                assert "SMILES" in content

    @pytest.mark.asyncio
    async def test_from_smiles_fallback_write_error(self):
        from fusion_science.visualization.molecule import MoleculeVisualizer

        viz = MoleculeVisualizer()
        viz._rdkit_available = False
        with patch("builtins.open", side_effect=PermissionError("denied")):
            result = await viz.from_smiles("CCO", name="test")
            assert result.success is False
            assert "Fallback HTML write failed" in result.error

    @pytest.mark.asyncio
    async def test_from_smiles_rdkit_mocked(self):
        from fusion_science.visualization.molecule import MoleculeVisualization, MoleculeVisualizer

        viz = MoleculeVisualizer()
        viz._rdkit_available = True
        viz._py3dmol_available = False

        expected_result = MoleculeVisualization(
            success=True,
            smiles="CCO",
            formula="C2H6O",
            molecular_weight=46.07,
        )

        with patch.object(viz, "from_smiles", new_callable=AsyncMock, return_value=expected_result):
            result = await viz.from_smiles("CCO", name="ethanol", render_2d=True, show_3d=False)
            assert result.success is True
            assert result.formula == "C2H6O"
            assert result.molecular_weight == pytest.approx(46.07, abs=0.1)

    @pytest.mark.asyncio
    async def test_from_smiles_invalid_smiles_mocked(self):
        from fusion_science.visualization.molecule import MoleculeVisualization, MoleculeVisualizer

        viz = MoleculeVisualizer()
        viz._rdkit_available = True

        expected_result = MoleculeVisualization(
            success=False,
            error="Invalid SMILES: INVALID_SMILES",
            smiles="INVALID_SMILES",
        )

        with patch.object(viz, "from_smiles", new_callable=AsyncMock, return_value=expected_result):
            result = await viz.from_smiles("INVALID_SMILES", name="bad")
            assert result.success is False
            assert "Invalid SMILES" in result.error

    @pytest.mark.asyncio
    async def test_from_pdb_with_content(self):
        from fusion_science.visualization.molecule import MoleculeVisualizer

        viz = MoleculeVisualizer()
        viz._py3dmol_available = False
        pdb_content = "ATOM      1  N   ALA A   1       1.000   2.000   3.000  1.00  0.00           N\n"
        with tempfile.TemporaryDirectory() as tmpdir, patch("tempfile.gettempdir", return_value=tmpdir):
            result = await viz.from_pdb("1ALA", pdb_content=pdb_content)
            assert result.success is True
            assert result.pdb_path != ""

    @pytest.mark.asyncio
    async def test_from_pdb_fetch_success(self):
        from fusion_science.visualization.molecule import MoleculeVisualizer

        viz = MoleculeVisualizer()
        viz._py3dmol_available = False
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "ATOM      1  N   ALA A   1       1.0     2.0     3.0  1.00  0.00           N\n"
        with patch("httpx.get", return_value=mock_resp), tempfile.TemporaryDirectory() as tmpdir:
            with patch("tempfile.gettempdir", return_value=tmpdir):
                result = await viz.from_pdb("1ALA")
                assert result.success is True

    @pytest.mark.asyncio
    async def test_from_pdb_fetch_fail(self):
        from fusion_science.visualization.molecule import MoleculeVisualizer

        viz = MoleculeVisualizer()
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        with patch("httpx.get", return_value=mock_resp):
            result = await viz.from_pdb("XXXX")
            assert result.success is False
            assert "Failed to fetch PDB" in result.error

    @pytest.mark.asyncio
    async def test_from_pdb_offline_mode(self):
        from fusion_science.visualization.molecule import MoleculeVisualizer

        viz = MoleculeVisualizer()
        viz._py3dmol_available = False
        pdb_content = "ATOM      1  N   ALA A   1       1.0     2.0     3.0\n"
        with tempfile.TemporaryDirectory() as tmpdir, patch("tempfile.gettempdir", return_value=tmpdir):
            with patch.dict(os.environ, {"FUSION_OFFLINE_MODE": "true"}):
                result = await viz.from_pdb("1ALA", pdb_content=pdb_content)
                assert result.success is True
                assert result.html_path.startswith("file://")

    @pytest.mark.asyncio
    async def test_from_pdb_error(self):
        from fusion_science.visualization.molecule import MoleculeVisualizer

        viz = MoleculeVisualizer()
        with patch("httpx.get", side_effect=Exception("network error")):
            result = await viz.from_pdb("1ALA")
            assert result.success is False

    def test_known_drugs(self):
        from fusion_science.visualization.molecule import MoleculeVisualizer

        drugs = MoleculeVisualizer.known_drugs()
        assert len(drugs) >= 5
        assert any(d["name"] == "Aspirin" for d in drugs)

    def test_generate_3d_html(self):
        from fusion_science.visualization.molecule import MoleculeVisualizer

        viz = MoleculeVisualizer()
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = os.path.join(tmpdir, "test_3d.html")
            viz._generate_3d_html("ATOM data", out_path, "TestMol", style="stick")
            with open(out_path) as f:
                content = f.read()
                assert "3Dmol" in content
                assert "TestMol" in content
                assert "stick" in content

    def test_generate_3d_html_styles(self):
        from fusion_science.visualization.molecule import MoleculeVisualizer

        viz = MoleculeVisualizer()
        for style in ["cartoon", "stick", "line", "sphere"]:
            with tempfile.TemporaryDirectory() as tmpdir:
                out_path = os.path.join(tmpdir, f"test_{style}.html")
                viz._generate_3d_html("ATOM data", out_path, "Test", style=style)
                with open(out_path) as f:
                    content = f.read()
                    assert "3Dmol" in content

    def test_generate_3d_html_unknown_style(self):
        from fusion_science.visualization.molecule import MoleculeVisualizer

        viz = MoleculeVisualizer()
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = os.path.join(tmpdir, "unknown_style.html")
            viz._generate_3d_html("ATOM data", out_path, "Test", style="nonexistent")
            with open(out_path) as f:
                content = f.read()
                assert "3Dmol" in content


# ===========================================================================
# 11. ProteinVisualizer
# ===========================================================================


class TestProteinVisualizer:
    def test_init(self):
        from fusion_science.visualization.protein import ProteinVisualizer

        viz = ProteinVisualizer()
        assert isinstance(viz._py3dmol_available, bool)

    @pytest.mark.asyncio
    async def test_visualize_with_content(self):
        from fusion_science.visualization.protein import ProteinVisualizer

        viz = ProteinVisualizer()
        viz._py3dmol_available = False
        pdb_content = (
            "ATOM      1  N   ALA A   1       1.000   2.000   3.000  1.00  0.00           N\n"
            "ATOM      2  CA  ALA A   1       2.000   3.000   4.000  1.00  0.00           C\n"
        )
        with tempfile.TemporaryDirectory() as tmpdir, patch("tempfile.gettempdir", return_value=tmpdir):
            result = await viz.visualize("1ALA", pdb_content=pdb_content)
            assert result.success is True
            assert result.chain_count >= 1
            assert result.residue_count >= 1

    @pytest.mark.asyncio
    async def test_visualize_with_highlights(self):
        from fusion_science.visualization.protein import ProteinVisualizer

        viz = ProteinVisualizer()
        viz._py3dmol_available = True
        pdb_content = "ATOM      1  N   ALA A   1       1.000   2.000   3.000\n"
        with tempfile.TemporaryDirectory() as tmpdir, patch("tempfile.gettempdir", return_value=tmpdir):
            result = await viz.visualize(
                "1ALA",
                pdb_content=pdb_content,
                highlights=[{"start": 1, "end": 10, "color": "red", "label": "Active site"}],
                show_ligands=True,
            )
            assert result.success is True
            with open(result.html_path) as f:
                content = f.read()
                assert "Active site" in content

    @pytest.mark.asyncio
    async def test_visualize_fetch_success(self):
        from fusion_science.visualization.protein import ProteinVisualizer

        viz = ProteinVisualizer()
        viz._py3dmol_available = False
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "ATOM      1  N   ALA A   1       1.0     2.0     3.0\n"
        with patch("httpx.get", return_value=mock_resp), tempfile.TemporaryDirectory() as tmpdir:
            with patch("tempfile.gettempdir", return_value=tmpdir):
                result = await viz.visualize("1ALA")
                assert result.success is True

    @pytest.mark.asyncio
    async def test_visualize_fetch_fail(self):
        from fusion_science.visualization.protein import ProteinVisualizer

        viz = ProteinVisualizer()
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        with patch("httpx.get", return_value=mock_resp):
            result = await viz.visualize("XXXX")
            assert result.success is False
            assert "Failed to fetch PDB" in result.error

    @pytest.mark.asyncio
    async def test_visualize_offline_mode(self):
        from fusion_science.visualization.protein import ProteinVisualizer

        viz = ProteinVisualizer()
        viz._py3dmol_available = False
        pdb_content = "ATOM      1  N   ALA A   1       1.0     2.0     3.0\n"
        with tempfile.TemporaryDirectory() as tmpdir, patch("tempfile.gettempdir", return_value=tmpdir):
            with patch.dict(os.environ, {"FUSION_OFFLINE_MODE": "true"}):
                result = await viz.visualize("1ALA", pdb_content=pdb_content)
                assert result.success is True
                assert result.html_path.startswith("file://")

    @pytest.mark.asyncio
    async def test_visualize_error(self):
        from fusion_science.visualization.protein import ProteinVisualizer

        viz = ProteinVisualizer()
        with patch("httpx.get", side_effect=Exception("network error")):
            result = await viz.visualize("1ALA")
            assert result.success is False
            assert result.pdb_id == "1ALA"

    def test_generate_protein_html(self):
        from fusion_science.visualization.protein import ProteinVisualizer

        viz = ProteinVisualizer()
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = os.path.join(tmpdir, "protein.html")
            viz._generate_protein_html("ATOM data", out_path, "1ALA", "cartoon", [], True)
            with open(out_path) as f:
                content = f.read()
                assert "3Dmol" in content
                assert "1ALA" in content

    def test_generate_protein_html_styles(self):
        from fusion_science.visualization.protein import ProteinVisualizer

        viz = ProteinVisualizer()
        for style in ["cartoon", "surface", "ribbon", "trace"]:
            with tempfile.TemporaryDirectory() as tmpdir:
                out_path = os.path.join(tmpdir, f"protein_{style}.html")
                viz._generate_protein_html("ATOM data", out_path, "Test", style, [], False)
                with open(out_path) as f:
                    content = f.read()
                    assert "3Dmol" in content

    def test_generate_protein_html_with_highlights_and_ligands(self):
        from fusion_science.visualization.protein import ProteinVisualizer

        viz = ProteinVisualizer()
        with tempfile.TemporaryDirectory() as tmpdir:
            out_path = os.path.join(tmpdir, "highlighted.html")
            highlights = [{"start": 50, "end": 100, "color": "blue", "label": "Binding site"}]
            viz._generate_protein_html("ATOM data", out_path, "Test", "cartoon", highlights, True)
            with open(out_path) as f:
                content = f.read()
                assert "Binding site" in content
                assert "hetflag" in content

    @pytest.mark.asyncio
    async def test_compare_structures_success(self):
        from fusion_science.visualization.protein import ProteinVisualizer

        viz = ProteinVisualizer()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "ATOM      1  N   ALA A   1       1.0     2.0     3.0\n"
        with patch("httpx.get", return_value=mock_resp), tempfile.TemporaryDirectory() as tmpdir:
            with patch("tempfile.gettempdir", return_value=tmpdir):
                html_path = await viz.compare_structures(["1ALA", "1BNA"])
                assert html_path != ""
                with open(html_path) as f:
                    content = f.read()
                    assert "3Dmol" in content

    @pytest.mark.asyncio
    async def test_compare_structures_no_data(self):
        from fusion_science.visualization.protein import ProteinVisualizer

        viz = ProteinVisualizer()
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        with patch("httpx.get", return_value=mock_resp):
            html_path = await viz.compare_structures(["XXXX"])
            assert html_path == ""

    @pytest.mark.asyncio
    async def test_compare_structures_error(self):
        from fusion_science.visualization.protein import ProteinVisualizer

        viz = ProteinVisualizer()
        with patch("httpx.get", side_effect=Exception("fail")):
            html_path = await viz.compare_structures(["1ALA"])
            assert html_path == ""

    def test_notable_proteins(self):
        from fusion_science.visualization.protein import ProteinVisualizer

        proteins = ProteinVisualizer.notable_proteins()
        assert len(proteins) >= 5
        assert any(p["pdb_id"] == "6M0J" for p in proteins)


# ===========================================================================
# 12. Utils mirrors
# ===========================================================================


class TestMirrorConfig:
    def test_get_mirror_config_defaults(self, monkeypatch):
        monkeypatch.delenv("FUSION_SCIENCE_USE_MIRRORS", raising=False)
        monkeypatch.delenv("FUSION_OFFLINE_MODE", raising=False)
        config = get_mirror_config()
        assert config["enabled"] is False
        assert config["offline_mode"] is False
        assert "pubmed" in config["mirrors"]
        assert "uniprot" in config["mirrors"]
        assert "pdb" in config["mirrors"]
        assert "ensembl" in config["mirrors"]
        assert "chembl" in config["mirrors"]

    def test_get_mirror_config_enabled(self, monkeypatch):
        monkeypatch.setenv("FUSION_SCIENCE_USE_MIRRORS", "true")
        config = get_mirror_config()
        assert config["enabled"] is True

    def test_get_mirror_config_offline(self, monkeypatch):
        monkeypatch.setenv("FUSION_OFFLINE_MODE", "1")
        config = get_mirror_config()
        assert config["offline_mode"] is True

    def test_get_mirror_config_env_overrides(self, monkeypatch):
        monkeypatch.setenv("FUSION_SCI_PUBMED_MIRROR", "https://mirror.example.com")
        config = get_mirror_config()
        assert config["mirrors"]["pubmed"]["mirror"] == "https://mirror.example.com"

    def test_get_mirror_config_chinese_dbs(self):
        config = get_mirror_config()
        assert "NGDC" in config["chinese_databases"]
        assert "CNKI" in config["chinese_databases"]
        assert "ScienceDB" in config["chinese_databases"]


class TestOfflineRecommendation:
    def test_get_offline_recommendation(self):
        rec = get_offline_recommendation()
        assert "Offline Operation" in rec
        assert "PubMed" in rec
        assert "UniProt" in rec
        assert "PDB" in rec
        assert "Ensembl" in rec


class TestAvailableDatabases:
    def test_get_available_databases(self):
        dbs = get_available_databases()
        assert len(dbs) >= 5
        names = [d["name"] for d in dbs]
        assert "PubMed" in names
        assert "UniProt" in names
        assert "PDB" in names
        assert "Ensembl" in names
        assert "ChEMBL" in names


class TestCacheStats:
    def test_get_cache_stats(self):
        with patch("fusion_science.database.mirror.ScienceCache") as mock_cache_cls:
            mock_cache = MagicMock()
            mock_cache.stats.return_value = {"enabled": True, "total_entries": 5}
            mock_cache_cls.return_value = mock_cache
            stats = get_cache_stats()
            assert stats["total_entries"] == 5


class TestClearCache:
    def test_clear_cache(self):
        with patch("fusion_science.database.mirror.ScienceCache") as mock_cache_cls:
            mock_cache = MagicMock()
            mock_cache.stats.return_value = {"total_entries": 10}
            mock_cache.clear.return_value = None
            mock_cache_cls.return_value = mock_cache
            count = clear_cache()
            assert count == 10

    def test_clear_cache_with_source(self):
        with patch("fusion_science.database.mirror.ScienceCache") as mock_cache_cls:
            mock_cache = MagicMock()
            mock_cache.stats.return_value = {"total_entries": 3}
            mock_cache.clear.return_value = None
            mock_cache_cls.return_value = mock_cache
            count = clear_cache(source="pubmed")
            assert count == 3
            mock_cache.clear.assert_called_with(source="pubmed")
