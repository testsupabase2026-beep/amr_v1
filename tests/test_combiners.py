"""
Baseline tests for the compound-combine layer (src/executor.py).

These lock the CURRENT behaviour so the matplotlib chart refactor cannot silently
change how sub-step results are merged / ranked / labelled. Pure-pandas functions —
no DB, no LLM, no network.
"""
import pandas as pd
import pytest

from src import executor as ex


# ── merge_on_key ──────────────────────────────────────────────
def test_merge_on_key_scalar_vs_scalar_labels_from_entity():
    """Two keyless scalars → 2-row labelled frame (brand A vs brand B)."""
    df1 = pd.DataFrame({"revenue_kwd": [100.0]})
    df2 = pd.DataFrame({"revenue_kwd": [200.0]})
    steps = ["total revenue for brand تايد in 2024",
             "total revenue for brand اريال in 2024"]
    out = ex._combine_merge_on_key([df1, df2], steps)
    assert list(out.columns) == ["label", "revenue_kwd"]
    assert len(out) == 2
    assert out["label"].tolist() == ["تايد", "اريال"]
    assert out["revenue_kwd"].tolist() == [100.0, 200.0]


def test_merge_on_key_keyed_period_suffixes():
    """Per-category revenue for two periods → suffixed columns, one row per key."""
    df1 = pd.DataFrame({"category_name": ["a", "b"], "revenue_kwd": [10.0, 20.0]})
    df2 = pd.DataFrame({"category_name": ["a", "b"], "revenue_kwd": [15.0, 25.0]})
    steps = ["revenue per category in 2024", "revenue per category in 2025"]
    out = ex._combine_merge_on_key([df1, df2], steps)
    assert len(out) == 2
    # both period-suffixed revenue columns present, no _x/_y collision
    cols = set(out.columns)
    assert "category_name" in cols
    assert any(c.endswith("2024") for c in cols)
    assert any(c.endswith("2025") for c in cols)
    assert not any(c.endswith(("_x", "_y")) for c in cols)


# ── pct_change: new-entrant guard ─────────────────────────────
def test_pct_change_zero_base_is_new_entrant_not_bogus_growth():
    """A near-zero base must yield NaN growth + is_new flag, never 180000%."""
    df1 = pd.DataFrame({"category_name": ["food", "cleaners"],
                        "revenue_kwd": [100.0, 2_900_000.0]})      # food ~ new
    df2 = pd.DataFrame({"category_name": ["food", "cleaners"],
                        "revenue_kwd": [12_500_000.0, 3_070_000.0]})
    steps = ["revenue per category in 2024", "revenue per category in 2025"]
    out = ex._combine_pct_change([df1, df2], steps)
    gcol = [c for c in out.columns if c.endswith("_growth_pct")][0]
    ncol = [c for c in out.columns if c.endswith("_is_new")][0]
    food = out[out["category_name"] == "food"].iloc[0]
    clean = out[out["category_name"] == "cleaners"].iloc[0]
    assert pd.isna(food[gcol])          # new entrant → NaN, not a huge number
    assert bool(food[ncol]) is True
    assert not pd.isna(clean[gcol])     # real grower keeps a value
    assert bool(clean[ncol]) is False
    assert round(float(clean[gcol]), 1) == pytest.approx(5.9, abs=0.2)


def test_pct_change_scalar_two_row_frame():
    df1 = pd.DataFrame({"revenue_kwd": [1_000_000.0]})
    df2 = pd.DataFrame({"revenue_kwd": [1_200_000.0]})
    steps = ["revenue in 2024", "revenue in 2025"]
    out = ex._combine_pct_change([df1, df2], steps)
    assert set(["Period", "Value", "growth_pct"]).issubset(out.columns)
    assert len(out) == 2
    assert out["growth_pct"].iloc[1] == pytest.approx(20.0)


# ── ratio ─────────────────────────────────────────────────────
def test_ratio_scalar_share_pct():
    part = pd.DataFrame({"revenue_kwd": [3_650_472.91]})
    whole = pd.DataFrame({"revenue_kwd": [6_470_000.0]})
    steps = ["food revenue Q1 2025", "total revenue Q1 2025"]
    out = ex._combine_ratio([part, whole], steps)
    assert "share_pct" in out.columns
    assert out["share_pct"].iloc[0] == pytest.approx(56.42, abs=0.1)


# ── filter_by_step1: value-overlap key matching ───────────────
def test_filter_by_step1_matches_mismatched_key_names_and_topn():
    """step1 uses en_name, step2 uses product_name — must still align by value overlap,
    trim to top-N entities, and keep step-2's (trend) columns."""
    df1 = pd.DataFrame({
        "name": ["اكسترا", "فيري", "تايد", "بونكس", "اومو"],
        "en_name": ["EXTRA", "FAIRY", "TIDE", "BONUX", "OMO"],
        "revenue_kwd": [312651, 219507, 184726, 90000, 50000],
    })
    rows = []
    for en in ["EXTRA", "FAIRY", "TIDE", "BONUX", "OMO"]:
        for m in range(1, 13):
            rows.append((en, m, 1000 + m))
    df2 = pd.DataFrame(rows, columns=["product_name", "month", "revenue_kwd"])
    out = ex._combine_filter_by_step1([df1, df2], [], top_n=3)
    assert out["product_name"].nunique() == 3
    assert set(out["product_name"].unique()) == {"EXTRA", "FAIRY", "TIDE"}
    assert len(out) == 3 * 12
    assert "month" in out.columns          # step-2 trend columns preserved


# ── _apply_top_n ──────────────────────────────────────────────
def test_apply_top_n_trims_ranking_by_measure():
    df = pd.DataFrame({"name": [f"p{i}" for i in range(20)],
                       "revenue_kwd": list(range(20))})
    out = ex._apply_top_n(df, {"top_n": 5})
    assert len(out) == 5
    assert set(out["revenue_kwd"]) == {19, 18, 17, 16, 15}


def test_apply_top_n_skips_time_series():
    """A month×category result must NOT be row-trimmed (would destroy the trend)."""
    rows = []
    for cat in ["a", "b", "c"]:
        for m in range(1, 13):
            rows.append((cat, m, 100))
    df = pd.DataFrame(rows, columns=["category_name", "month", "revenue_kwd"])
    out = ex._apply_top_n(df, {"top_n": 5})
    assert len(out) == len(df)             # unchanged


# ── entity / period label extraction ──────────────────────────
@pytest.mark.parametrize("text,expected", [
    ("Calculate total revenue for brand تايد in 2024", "تايد"),
    ("Calculate total revenue for brand EXTRA in 2024", "EXTRA"),
    ("إجمالي إيرادات تايد في 2024", "تايد"),
])
def test_extract_entity_label(text, expected):
    assert ex._extract_entity_label(text, "") == expected


@pytest.mark.parametrize("text,expected", [
    ("total revenue for the first quarter of 2024", "Q1 2024"),
    ("revenue in 2025", "2025"),
])
def test_extract_period_label(text, expected):
    assert ex._extract_period_label(text, "fallback") == expected


# ── measure column detection (all-NULL SUM safety) ────────────
def test_numeric_or_measure_cols_catches_all_null_sum():
    df = pd.DataFrame({"revenue_kwd": [None]})   # NULL SUM → object dtype
    assert "revenue_kwd" in ex._numeric_or_measure_cols(df)
