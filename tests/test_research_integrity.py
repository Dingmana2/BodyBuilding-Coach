"""Tests for citation integrity + the synthesis/judge LLM wrappers (eng-review T5-T7, T11).

The deterministic integrity gate is the load-bearing trust component, so it gets
full unit coverage. The two LLM wrappers are tested for parse/robustness with a
mocked Anthropic client (offline). A live claim-match eval lives in
test_research_evals.py and is skipped without a real API key.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import claude_service
import research_service as rs


def _fake_message(text: str):
    return SimpleNamespace(content=[SimpleNamespace(text=text)])


# ── T5: deterministic PMID-membership ───────────────────────────────────────────

def test_extract_cited_pmids():
    pmids = rs.extract_cited_pmids("Do incline [PMID:12345]; also PMID: 678 supports this.")
    assert pmids == ["12345", "678"]


def test_verify_keeps_allowed_drops_others():
    report = {
        "weak_point": "weakpoint:upper_chest:bulk",
        "summary": "Incline press helps. AI estimate — not medical advice.",
        "citations": [{"pmid": "111", "claim": "x"}, {"pmid": "222", "claim": "y"}],
    }
    out = rs.verify_report_citations(report, {"111"})
    kept = [c["pmid"] for c in out["citations"]]
    assert kept == ["111"]          # 222 dropped (not in allowed set)
    assert out["degraded"] is False


def test_verify_degrades_on_empty_never_fabricates():
    report = {"weak_point": "w", "summary": "Cite [PMID:999].", "citations": [{"pmid": "999"}]}
    out = rs.verify_report_citations(report, set())  # judge rejected everything
    assert out["citations"] == []
    assert out["degraded"] is True
    assert "no" in out["summary"].lower()           # honest note, not a fabricated cite
    assert "999" not in out["summary"]


def test_verify_strips_orphan_pmid_from_summary():
    report = {
        "weak_point": "w",
        "summary": "Try incline [PMID:999] and add volume [PMID:111].",
        "citations": [{"pmid": "111", "claim": "volume"}],
    }
    out = rs.verify_report_citations(report, {"111"})
    assert "999" not in out["summary"]   # orphan stripped
    assert "111" in out["summary"]       # verified citation kept


# ── T4: synthesis wrapper (mocked client) ───────────────────────────────────────

def test_synthesize_parses_reports():
    payload = '{"reports": [{"weak_point": "weakpoint:upper_chest:bulk", "summary": "Incline. AI estimate — not medical advice.", "citations": [{"pmid": "111", "claim": "upper pec activation"}]}]}'
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _fake_message(payload)
    with patch.object(claude_service, "_client", return_value=fake_client):
        out = claude_service.synthesize_weakpoint_report(
            [{"weak_point": "weakpoint:upper_chest:bulk", "goal": "bulk",
              "papers": [{"pmid": "111", "title": "t", "year": "2021",
                          "evidence_label": "RCT", "abstract": "a"}]}]
        )
    assert out["reports"][0]["citations"][0]["pmid"] == "111"


def test_synthesize_empty_blocks_skips_call():
    fake_client = MagicMock()
    with patch.object(claude_service, "_client", return_value=fake_client):
        assert claude_service.synthesize_weakpoint_report([]) == {"reports": []}
    fake_client.messages.create.assert_not_called()


# ── T6: claim-match judge wrapper (mocked client) ───────────────────────────────

def test_judge_parses_results():
    payload = '{"results": [{"pmid": "1", "supported": true}, {"pmid": "2", "supported": false}]}'
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _fake_message(payload)
    with patch.object(claude_service, "_client", return_value=fake_client):
        out = claude_service.judge_citation_claims(
            [{"pmid": "1", "claim": "c", "abstract": "a"},
             {"pmid": "2", "claim": "c", "abstract": "a"}]
        )
    assert out == {"1": True, "2": False}


def test_judge_empty_pairs_skips_call():
    fake_client = MagicMock()
    with patch.object(claude_service, "_client", return_value=fake_client):
        assert claude_service.judge_citation_claims([]) == {}
    fake_client.messages.create.assert_not_called()


def test_judge_unmarked_pmid_defaults_false_at_caller():
    # Judge omits a pmid → caller treats it as unsupported (conservative).
    payload = '{"results": [{"pmid": "1", "supported": true}]}'
    fake_client = MagicMock()
    fake_client.messages.create.return_value = _fake_message(payload)
    with patch.object(claude_service, "_client", return_value=fake_client):
        out = claude_service.judge_citation_claims(
            [{"pmid": "1", "claim": "c", "abstract": "a"},
             {"pmid": "2", "claim": "c", "abstract": "a"}]
        )
    assert out.get("2") is None          # absent → caller's allowed-set excludes it
