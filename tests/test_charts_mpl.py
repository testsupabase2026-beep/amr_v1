"""
Tests for the static matplotlib chart renderer (utils/charts_mpl.py) and the shared
spec data-prep (utils/formatting._prepare_spec_df).

A valid render returns non-empty PNG bytes whose header is the PNG magic number.
"""
import pandas as pd
import pytest

from utils import charts_mpl as cm
from utils.formatting import _prepare_spec_df

_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def _is_png(b):
    return isinstance(b, (bytes, bytearray)) and b[:8] == _PNG_MAGIC and len(b) > 500


# ── _prepare_spec_df (shared transforms) ──────────────────────
def test_prepare_dropna_agg_topn_sort():
    df = pd.DataFrame({
        "category_name": ["a", "a", "b", "c", "d", "e", "f"],
        "revenue_kwd": [5, 5, 8, 3, 9, 1, 7],
    })
    # agg sums the duplicate 'a' rows → 10; top_n=3 by measure; hbar → measure is x
    spec = {"kind": "bar", "x": "revenue_kwd", "y": "category_name",
            "orientation": "h", "agg": "sum", "top_n": 3,
            "sort_by": "revenue_kwd", "ascending": True}
    out = _prepare_spec_df(df, spec)
    assert len(out) == 3
    assert set(out["category_name"]) == {"a", "b", "d"}   # top 3 by summed revenue (10,8,9)


def test_prepare_dropna_growth():
    df = pd.DataFrame({"category_name": ["a", "b", "c"],
                       "revenue_kwd_growth_pct": [5.0, None, 12.0]})
    spec = {"kind": "bar", "x": "revenue_kwd_growth_pct", "y": "category_name",
            "orientation": "h", "dropna": "revenue_kwd_growth_pct"}
    out = _prepare_spec_df(df, spec)
    assert len(out) == 2                                  # NaN row dropped
    assert "b" not in set(out["category_name"])


# ── render_spec: every kind → valid PNG ───────────────────────
def test_render_multiline_trend():
    rows = []
    for prod in ["A", "B", "C"]:
        for m in range(1, 13):
            rows.append((prod, m, 100 + m))
    df = pd.DataFrame(rows, columns=["product_name", "month", "revenue_kwd"])
    spec = {"kind": "line", "x": "month", "y": "revenue_kwd",
            "color": "product_name", "sort_by": "month",
            "title": "trend", "x_title": "", "y_title": "الإيرادات"}
    png, path = cm.render_spec(df, spec)
    assert _is_png(png)


def test_render_single_line():
    df = pd.DataFrame({"month_name": ["Jan", "Feb", "Mar"], "revenue_kwd": [10, 30, 20]})
    spec = {"kind": "line", "x": "month_name", "y": "revenue_kwd", "title": "t"}
    png, _ = cm.render_spec(df, spec)
    assert _is_png(png)


def test_render_hbar_ranking():
    df = pd.DataFrame({"category_name": ["المنظفات", "المناديل", "العناية"],
                       "revenue_kwd": [900, 500, 300]})
    spec = {"kind": "bar", "x": "revenue_kwd", "y": "category_name",
            "orientation": "h", "sort_by": "revenue_kwd", "title": "رتب"}
    png, _ = cm.render_spec(df, spec)
    assert _is_png(png)


def test_render_grouped_wide_comparison():
    df = pd.DataFrame({"category_name": ["a", "b"],
                       "revenue_kwd_2024": [100, 200],
                       "revenue_kwd_2025": [150, 180]})
    spec = {"kind": "bar", "x": "category_name",
            "y": ["revenue_kwd_2024", "revenue_kwd_2025"],
            "orientation": "v", "barmode": "group", "title": "compare"}
    png, _ = cm.render_spec(df, spec)
    assert _is_png(png)


def test_render_pie():
    df = pd.DataFrame({"category_name": ["a", "b", "c"], "revenue_kwd": [50, 30, 20]})
    spec = {"kind": "pie", "names": "category_name", "values": "revenue_kwd", "title": "share"}
    png, _ = cm.render_spec(df, spec)
    assert _is_png(png)


def test_render_saves_file(tmp_path):
    df = pd.DataFrame({"category_name": ["a", "b"], "revenue_kwd": [1, 2]})
    spec = {"kind": "bar", "x": "revenue_kwd", "y": "category_name", "orientation": "h"}
    png, path = cm.render_spec(df, spec, out_dir=str(tmp_path))
    assert _is_png(png)
    assert path and path.endswith(".png")
    import os
    assert os.path.exists(path)


def test_render_empty_df_returns_none():
    df = pd.DataFrame({"category_name": [], "revenue_kwd": []})
    png, path = cm.render_spec(df, {"kind": "bar", "x": "revenue_kwd", "y": "category_name"})
    assert png is None and path is None


# ── render_auto (heuristic fallback) ──────────────────────────
def test_render_auto_timeseries_to_line():
    rows = [("a", m, 100 + m) for m in range(1, 13)]
    df = pd.DataFrame(rows, columns=["category_name", "month", "revenue_kwd"])
    png, _ = cm.render_auto(df, {"intent_type": "trend"}, "trend q")
    assert _is_png(png)


def test_render_auto_single_year_trend_ignores_year_month_as_measure():
    """Regression: a single-year monthly trend (year+month+month_name+revenue) must plot
    revenue — NOT the constant `year` — and treat year/month as time, not measures."""
    df = pd.DataFrame({
        "year": [2024] * 12,
        "month": list(range(1, 13)),
        "month_name": ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                       "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
        "revenue_kwd": [2000.0 + i * 100 for i in range(12)],
    })
    # measure detection must exclude year & month
    assert cm._measure_cols(df) == ["revenue_kwd"]
    png, _ = cm.render_auto(df, {"intent_type": "trend"}, "monthly 2024")
    assert _is_png(png)


# ── new chart kinds: donut + area ─────────────────────────────
def test_render_donut():
    df = pd.DataFrame({"category_name": ["a", "b", "c", "d", "e", "f", "g"],
                       "revenue_kwd": [40, 25, 15, 8, 6, 4, 2]})
    png, _ = cm.render_spec(df, {"kind": "donut", "names": "category_name",
                                 "values": "revenue_kwd", "title": "share"})
    assert _is_png(png)


def test_render_area_single():
    df = pd.DataFrame({"month_name": ["Jan", "Feb", "Mar", "Apr"],
                       "month": [1, 2, 3, 4], "revenue_kwd": [10, 25, 18, 40]})
    png, _ = cm.render_spec(df, {"kind": "area", "x": "month_name", "y": "revenue_kwd",
                                 "sort_by": "month", "title": "trend"})
    assert _is_png(png)


def test_render_area_stacked_multiseries():
    rows = []
    for cat in ["a", "b"]:
        for m in range(1, 6):
            rows.append((m, f"M{m}", 10 * m if cat == "a" else 5 * m, cat))
    df = pd.DataFrame(rows, columns=["month", "month_name", "revenue_kwd", "category_name"])
    png, _ = cm.render_spec(df, {"kind": "area", "x": "month_name", "y": "revenue_kwd",
                                 "color": "category_name", "sort_by": "month", "title": "stacked"})
    assert _is_png(png)


def test_donut_kind_alias_adds_hole():
    """kind='donut' with no explicit hole must still render a hole (via dispatch default)."""
    df = pd.DataFrame({"c": ["a", "b"], "v": [3, 1]})
    png, _ = cm.render_spec(df, {"kind": "donut", "names": "c", "values": "v"})
    assert _is_png(png)


def test_pie_value_col_prefers_measure_over_pct():
    """A share-of-total result carries revenue AND a pct column — the pie must size slices by
    the raw measure, not the percentage. (Regression: distribution branch required 1 numeric.)"""
    df = pd.DataFrame({"category_name": ["a", "b", "c"],
                       "revenue_kwd": [500, 300, 200], "pct_of_total": [50.0, 30.0, 20.0]})
    # simulate the enrichment selection of the value column
    numeric = ["revenue_kwd", "pct_of_total"]
    pct_like = [c for c in numeric if any(k in c.lower() for k in ("pct", "percent", "share", "ratio"))]
    value = [c for c in numeric if c not in pct_like][0]
    assert value == "revenue_kwd"
    png, _ = cm.render_spec(df, {"kind": "donut", "names": "category_name",
                                 "values": value, "title": "share"})
    assert _is_png(png)


def test_measure_cols_excludes_time_and_id():
    df = pd.DataFrame({"year": [2024], "month": [1], "product_key": [5],
                       "revenue_kwd": [10.0], "quantity": [3]})
    assert set(cm._measure_cols(df)) == {"revenue_kwd", "quantity"}


def test_render_auto_categorical_to_bar():
    df = pd.DataFrame({"category_name": ["a", "b", "c"], "revenue_kwd": [3, 1, 2]})
    png, _ = cm.render_auto(df, {"intent_type": "ranking"}, "rank q")
    assert _is_png(png)
