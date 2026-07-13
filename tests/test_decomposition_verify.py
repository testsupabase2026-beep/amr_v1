"""
Tests for the compound-decomposition verifier (src.intent._verify_decomposition).

The verifier makes one LLM call; we replace the LLM with a fake Runnable that returns a
canned JSON string, so these run offline and deterministically. We assert the verifier
APPLIES a valid correction, is a NO-OP when nothing changed, and falls back safely on junk.
"""
import asyncio
import json

import pytest
from langchain_core.runnables import RunnableLambda

import src.intent as intent


def _run(coro):
    return asyncio.run(coro)


class _FakeLLM(RunnableLambda):
    """Stands in for the ChatGroq runnable: ignores input, returns a fixed string."""
    def __init__(self, payload: str):
        super().__init__(lambda _x: payload)


def _patch_llm(monkeypatch, payload_dict_or_str):
    payload = payload_dict_or_str if isinstance(payload_dict_or_str, str) else json.dumps(payload_dict_or_str)
    monkeypatch.setattr(intent, "llm", _FakeLLM(payload))


# ── applies a valid correction (merge_on_key → ratio) ─────────
def test_verifier_corrects_to_ratio(monkeypatch):
    _patch_llm(monkeypatch, {
        "is_compound": True,
        "steps": ["food revenue Q1 2025", "total revenue Q1 2025"],
        "combination": "ratio",
        "changed": True, "reason": "share of total",
    })
    plan = {"is_compound": True,
            "steps": ["food revenue Q1 2025", "total revenue Q1 2025"],
            "combination": "display_separately"}
    out, note = _run(intent._verify_decomposition("ما نسبة إيرادات المواد الغذائية من الإجمالي؟", plan))
    assert out["combination"] == "ratio"
    assert "display_separately → ratio" in note


# ── corrects to filter_by_step1 for a trend ───────────────────
def test_verifier_corrects_to_filter_by_step1(monkeypatch):
    _patch_llm(monkeypatch, {
        "is_compound": True,
        "steps": ["top 5 products by revenue 2025", "monthly revenue per product 2025"],
        "combination": "filter_by_step1",
        "changed": True, "reason": "trend",
    })
    plan = {"is_compound": True,
            "steps": ["top 5 products 2025", "revenue per product 2025"],
            "combination": "merge_on_key"}
    out, note = _run(intent._verify_decomposition("top 5 products and their monthly trend", plan))
    assert out["combination"] == "filter_by_step1"
    assert out["steps"][1].startswith("monthly")


# ── downgrades a 3+ period query to simple ────────────────────
def test_verifier_downgrades_to_simple(monkeypatch):
    _patch_llm(monkeypatch, {
        "is_compound": False,
        "steps": ["revenue per category for months 1,2,3 in 2025"],
        "combination": "display_separately",
        "changed": True, "reason": "3 periods → single query",
    })
    plan = {"is_compound": True,
            "steps": ["jan per category", "feb per category"],
            "combination": "merge_on_key"}
    out, note = _run(intent._verify_decomposition("compare Jan Feb Mar per category", plan))
    assert out["is_compound"] is False
    assert "compound → simple" in note


# ── no-op when the plan is already correct ────────────────────
def test_verifier_noop_when_unchanged(monkeypatch):
    steps = ["revenue per category 2024", "revenue per category 2025"]
    _patch_llm(monkeypatch, {
        "is_compound": True, "steps": steps, "combination": "pct_change",
        "changed": False, "reason": "",
    })
    plan = {"is_compound": True, "steps": steps, "combination": "pct_change"}
    out, note = _run(intent._verify_decomposition("category revenue growth 2024 to 2025", plan))
    assert out["combination"] == "pct_change"
    assert note == ""


# ── junk / invalid combination → safe fallback to original ────
def test_verifier_rejects_invalid_combination(monkeypatch):
    _patch_llm(monkeypatch, {
        "is_compound": True, "steps": ["a", "b"], "combination": "compare",  # invalid
        "changed": True, "reason": "x",
    })
    plan = {"is_compound": True, "steps": ["a", "b"], "combination": "merge_on_key"}
    out, note = _run(intent._verify_decomposition("q", plan))
    assert out["combination"] == "merge_on_key"   # unchanged
    assert note == ""


def test_verifier_handles_non_json(monkeypatch):
    _patch_llm(monkeypatch, "sorry I cannot help with that")
    plan = {"is_compound": True, "steps": ["a", "b"], "combination": "merge_on_key"}
    out, note = _run(intent._verify_decomposition("q", plan))
    assert out == plan
    assert note == ""
