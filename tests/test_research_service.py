"""Unit tests for the research-integration foundation (eng-review T1-T3).

Covers evidence-grade extraction/classification, weak-point query mapping,
grade-first/recency-tiebreak ranking, and the critical no-fabrication-on-empty
contract. All pure/offline — no network, no Claude, no DB.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import research_service as rs


# ── T1: evidence-grade classification ──────────────────────────────────────────

def test_classify_grade_meta_analysis_is_top():
    assert rs.classify_evidence_grade(["Meta-Analysis"]) == 5
    assert rs.classify_evidence_grade(["Systematic Review"]) == 5


def test_classify_grade_rct_below_meta():
    assert rs.classify_evidence_grade(["Randomized Controlled Trial"]) == 4


def test_classify_grade_picks_strongest_of_mixed_types():
    # A paper tagged both — the strongest type wins.
    assert rs.classify_evidence_grade(["Journal Article", "Meta-Analysis"]) == 5


def test_classify_grade_empty_and_unknown_default():
    assert rs.classify_evidence_grade([]) == rs.DEFAULT_GRADE
    assert rs.classify_evidence_grade(["Some Future Type"]) == rs.DEFAULT_GRADE


def test_classify_grade_low_tier():
    assert rs.classify_evidence_grade(["Editorial"]) == 0
    assert rs.classify_evidence_grade(["Letter"]) == 0


def test_parse_pubmed_xml_extracts_grade_and_pmid():
    xml = """<PubmedArticleSet><PubmedArticle><MedlineCitation>
      <PMID>12345</PMID>
      <Article>
        <ArticleTitle>Incline press and upper pectoralis activation</ArticleTitle>
        <Journal><Title>J Strength Cond</Title>
          <JournalIssue><PubDate><Year>2021</Year></PubDate></JournalIssue></Journal>
        <Abstract><AbstractText>Incline pressing increased upper pectoralis activation.</AbstractText></Abstract>
        <AuthorList><Author><LastName>Smith</LastName><ForeName>Jane</ForeName></Author></AuthorList>
        <PublicationTypeList>
          <PublicationType>Meta-Analysis</PublicationType>
          <PublicationType>Journal Article</PublicationType>
        </PublicationTypeList>
      </Article>
    </MedlineCitation></PubmedArticle></PubmedArticleSet>"""
    papers = rs._parse_pubmed_xml(xml)
    assert len(papers) == 1
    p = papers[0]
    assert p["pmid"] == "12345"
    assert p["evidence_grade"] == 5
    assert "Meta-Analysis" in p["publication_types"]
    assert p["url"] == "https://pubmed.ncbi.nlm.nih.gov/12345/"


def test_parse_pubmed_xml_malformed_returns_empty():
    assert rs._parse_pubmed_xml("<not valid xml") == []


# ── T2: weak-point query mapping ────────────────────────────────────────────────

def test_weakpoint_query_known_weak_point():
    q = rs.weakpoint_query("upper chest", "bulk")
    assert "incline" in q
    assert "hypertrophy" in q  # goal hint appended for bulk


def test_weakpoint_query_substring_match():
    # Free-text analysis output still resolves via substring.
    q = rs.weakpoint_query("slightly underdeveloped upper chest", "bulk")
    assert "incline" in q


def test_weakpoint_query_unknown_falls_back_to_raw():
    q = rs.weakpoint_query("weird muscle", "")
    assert q == "weird muscle hypertrophy training"


def test_weakpoint_query_empty():
    assert rs.weakpoint_query("", "") == "muscle hypertrophy training"


def test_weakpoint_topic_key_normalizes():
    assert rs.weakpoint_topic_key("upper chest", "bulk") == "weakpoint:upper_chest:bulk"
    assert rs.weakpoint_topic_key("Upper Chest", "") == "weakpoint:upper_chest"


# ── T2: grade-first ranking (office-hours D4) ───────────────────────────────────

def test_grade_rank_grade_beats_recency():
    papers = [
        {"pmid": "a", "evidence_grade": 1, "year": "2024"},  # new single study
        {"pmid": "b", "evidence_grade": 5, "year": "2019"},  # old meta-analysis
        {"pmid": "c", "evidence_grade": 4, "year": "2023"},  # RCT
        {"pmid": "d", "evidence_grade": 1, "year": "2025"},  # newest single study
    ]
    ordered = [p["pmid"] for p in rs.grade_rank(papers)]
    # meta-analysis first despite being oldest; among grade-1, newer (d) before (a)
    assert ordered == ["b", "c", "d", "a"]


def test_grade_rank_handles_missing_fields():
    papers = [{"pmid": "x"}, {"pmid": "y", "evidence_grade": 5, "year": "2020"}]
    ordered = [p["pmid"] for p in rs.grade_rank(papers)]
    assert ordered[0] == "y"  # graded paper outranks the default-grade one


def test_grade_rank_bad_year_does_not_crash():
    papers = [{"pmid": "x", "evidence_grade": 2, "year": "n/a"}]
    assert rs.grade_rank(papers)[0]["pmid"] == "x"


# ── T3: fetch degrades on empty, never fabricates (CRITICAL) ────────────────────

def test_fetch_weakpoint_research_degrades_on_empty():
    with (
        patch.object(rs, "search_pubmed", new=AsyncMock(return_value=[])),
        patch.object(rs, "search_semantic_scholar", new=AsyncMock(return_value=[])),
    ):
        result = asyncio.run(rs.fetch_weakpoint_research("upper chest", "bulk"))
    assert result["topic"] == "weakpoint:upper_chest:bulk"
    assert result["papers"] == []          # empty, NOT fabricated
    assert "incline" in result["query"]


def test_fetch_weakpoint_research_ranks_and_dedups():
    pubmed = [
        {"title": "Incline EMG", "evidence_grade": 1, "year": "2024"},
        {"title": "Meta upper chest", "evidence_grade": 5, "year": "2018"},
        {"title": "Incline EMG", "evidence_grade": 1, "year": "2024"},  # dup title
    ]
    semantic = [{"title": "Semantic finding", "year": 2022}]  # no grade → default
    with (
        patch.object(rs, "search_pubmed", new=AsyncMock(return_value=pubmed)),
        patch.object(rs, "search_semantic_scholar", new=AsyncMock(return_value=semantic)),
    ):
        result = asyncio.run(rs.fetch_weakpoint_research("upper chest", "bulk"))
    titles = [p["title"] for p in result["papers"]]
    assert titles[0] == "Meta upper chest"          # grade-first
    assert titles.count("Incline EMG") == 1          # deduped
    assert result["papers"][-1].get("evidence_grade") == rs.DEFAULT_GRADE  # SS default-graded
