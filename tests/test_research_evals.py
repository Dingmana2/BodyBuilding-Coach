"""Live eval for the claim-match judge (research-integration T11).

Offline (default / CI without a real key): validates the dataset shape only.
With a real ANTHROPIC_API_KEY set, it runs the judge against the labelled
adversarial cases and asserts the verdicts match — this is the gate that protects
the verifier-of-the-verifier. Wire this test into CI with a real key to catch
prompt/model drift over time.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

import claude_service

_CASES_PATH = Path(__file__).parent / "evals" / "claim_match_cases.json"


def _load_cases() -> list:
    data = json.loads(_CASES_PATH.read_text(encoding="utf-8"))
    return data["cases"]


def test_eval_dataset_is_well_formed():
    cases = _load_cases()
    assert len(cases) >= 4
    for c in cases:
        assert set(c) >= {"id", "pmid", "claim", "abstract", "expected_supported"}
        assert isinstance(c["expected_supported"], bool)


_HAVE_REAL_KEY = not os.getenv("ANTHROPIC_API_KEY", "sk-ant-test").startswith("sk-ant-test")


@pytest.mark.skipif(not _HAVE_REAL_KEY, reason="needs a real ANTHROPIC_API_KEY to run the live judge")
def test_claim_match_judge_matches_labels():
    cases = _load_cases()
    pairs = [{"pmid": c["pmid"], "claim": c["claim"], "abstract": c["abstract"]} for c in cases]
    verdicts = claude_service.judge_citation_claims(pairs)
    wrong = [
        c["id"] for c in cases
        if verdicts.get(c["pmid"], False) != c["expected_supported"]
    ]
    # Allow at most one miss across the adversarial set; tighten as the prompt matures.
    assert len(wrong) <= 1, f"judge disagreed on: {wrong}"
