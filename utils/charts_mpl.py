"""
charts_mpl.py
=============
Static chart renderer — matplotlib + seaborn. Replaces the interactive Plotly engine.

Two public entry points, both returning `(png_bytes, png_path | None)`:

  • render_spec(df, spec, ...)      — deterministic charts from a chart_spec dict
                                       (the same spec contract used by build_fig_from_spec).
  • render_auto(df, intent, ...)    — heuristic fallback for shapes with no spec
                                       (replaces the old LLM-writes-Plotly-code path;
                                        NO code execution).

Design notes
------------
- Uses the Agg backend (headless — safe on servers, no display needed).
- Arabic text is reshaped AND BiDi-corrected (`_ar_str`) — matplotlib, unlike Plotly,
  does not apply the BiDi algorithm itself, so labels must be pre-shaped.
- The NAMAA indigo colorway is mirrored from config so charts match the brand.
- Data transforms (dropna / agg / top-N / sort) are delegated to the SHARED
  `utils.formatting._prepare_spec_df`, so every combiner/top-N/new-entrant fix that
  the Plotly path had is preserved verbatim.
"""
from __future__ import annotations

import io
import os
import uuid

import matplotlib
matplotlib.use("Agg")                      # headless, no display / GUI thread
import matplotlib.pyplot as plt            # noqa: E402
import matplotlib.font_manager as fm       # noqa: E402
import pandas as pd                        # noqa: E402

from utils.arabic import _ar_str           # reshape + BiDi (matplotlib-correct)

# ── NAMAA brand theme (single source of truth in config) ──────
try:
    from src.config import NAMAA_TEMPLATE as _THEME
except Exception:                          # pragma: no cover - config not importable in isolation
    _THEME = {}

_COLORWAY = _THEME.get("colorway") or [
    "#3D1B6A", "#06b6d4", "#1e40af", "#f59e0b",
    "#84cc16", "#eab308", "#8A45B2", "#4E3074",
]
_FONT_FAMILY   = _THEME.get("font_family", "Segoe UI, Arial, sans-serif")
_FONT_COLOR    = _THEME.get("font_color", "#030213")
_TITLE_COLOR   = _THEME.get("title_color", "#030213")
_TITLE_SIZE    = _THEME.get("title_size", 13)
_TICK_COLOR    = _THEME.get("tick_color", "#717182")
_TICK_SIZE     = _THEME.get("tick_size", 10)
_PAPER_BG      = _THEME.get("paper_bgcolor", "#ffffff")
_PLOT_BG       = _THEME.get("plot_bgcolor", "#ffffff")
_GRID_COLOR    = _THEME.get("grid_color", "#e9e9ef")
_AXIS_LINE     = _THEME.get("axis_line_color", "rgba(0,0,0,0.10)")
_LEGEND_BG     = _THEME.get("legend_bgcolor", "#ffffff")
_LEGEND_BORDER = _THEME.get("legend_border", "rgba(0,0,0,0.10)")

_FIG_W_IN = 9.0        # inches; * dpi below → pixel size
_FIG_H_IN = 4.6
_DPI = 100             # 9x4.6 @ 100 → 900x460 px (matches old CHART_PNG_WIDTH/HEIGHT)


# ── Arabic-capable font (best-effort; falls back silently) ────
def _install_arabic_font():
    plt.rcParams["axes.edgecolor"] = _AXIS_LINE
    plt.rcParams["text.color"] = _FONT_COLOR
    for path in (
        r"C:\Windows\Fonts\arial.ttf",
        r"C:\Windows\Fonts\tahoma.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/arial.ttf",
    ):
        if os.path.exists(path):
            try:
                fm.fontManager.addfont(path)
                name = fm.FontProperties(fname=path).get_name()
                plt.rcParams["font.family"] = name
                return
            except Exception:
                continue


_install_arabic_font()


_TIME_PART_NAMES = ("year", "month", "week", "day", "quarter")


def _is_id_col(c) -> bool:
    cl = str(c).lower()
    return cl.endswith(("_key", "_id")) or cl in ("id", "key")


def _is_time_part_col(c) -> bool:
    """year/month/week/day/quarter are numeric by dtype but are TIME DIMENSIONS, not measures."""
    cl = str(c).lower()
    return any(cl == t or cl.endswith("_" + t) for t in _TIME_PART_NAMES)


def _measure_cols(df) -> list:
    """Numeric columns that are real MEASURES — excludes id/FK and time-part columns
    (year/month/…). Prevents plotting the year as a value."""
    return [
        c for c in df.select_dtypes(include="number").columns
        if not _is_id_col(c) and not _is_time_part_col(c)
    ]


def _t(text) -> str:
    """Prepare a label for matplotlib: Arabic reshaped + BiDi, others untouched."""
    s = "" if text is None else str(text)
    # Only reshape when there are Arabic characters (cheap guard).
    if any("؀" <= ch <= "ۿ" for ch in s):
        return _ar_str(s)
    return s


def _fig_to_png(fig) -> bytes:
    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=_DPI, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    buf.seek(0)
    return buf.getvalue()


def _save(png: bytes, out_dir: str | None) -> str | None:
    if not out_dir:
        return None
    try:
        path = os.path.join(out_dir, f"temp_chart_{uuid.uuid4().hex[:8]}.png")
        with open(path, "wb") as f:
            f.write(png)
        return path
    except Exception:
        return None


def _brand_legend(ax, **kw):
    """A legend styled with the brand background/border."""
    leg = ax.legend(**kw)
    if leg is not None:
        frame = leg.get_frame()
        frame.set_facecolor(_LEGEND_BG)
        frame.set_edgecolor(_LEGEND_BORDER)
        frame.set_linewidth(1)
    return leg


def _new_axes():
    fig, ax = plt.subplots(figsize=(_FIG_W_IN, _FIG_H_IN))
    fig.patch.set_facecolor(_PAPER_BG)
    ax.set_facecolor(_PLOT_BG)
    ax.grid(True, axis="both", color=_GRID_COLOR, linewidth=0.8, zorder=0)
    ax.set_axisbelow(True)
    for spine in ("top", "right"):
        ax.spines[spine].set_visible(False)
    for spine in ("left", "bottom"):
        ax.spines[spine].set_color(_AXIS_LINE)
    ax.tick_params(colors=_TICK_COLOR, labelsize=_TICK_SIZE)
    return fig, ax


def _finish(fig, ax, spec, out_dir):
    title = spec.get("title")
    if title:
        ax.set_title(_t(title), fontsize=_TITLE_SIZE, color=_TITLE_COLOR, pad=12)
    xt, yt = spec.get("x_title"), spec.get("y_title")
    if xt is not None:
        ax.set_xlabel(_t(xt), fontsize=11, color=_FONT_COLOR)
    if yt is not None:
        ax.set_ylabel(_t(yt), fontsize=11, color=_FONT_COLOR)
    png = _fig_to_png(fig)
    return png, _save(png, out_dir)


# ── Spec renderer ─────────────────────────────────────────────
def render_spec(df, spec: dict, out_dir: str | None = None):
    """Render a chart_spec dict to a static PNG. Returns (png_bytes, png_path|None) or
    (None, None) on failure. Data transforms reuse formatting._prepare_spec_df."""
    from utils.formatting import _prepare_spec_df
    try:
        d = _prepare_spec_df(df, spec)
        if d is None or len(d) == 0:
            return None, None
        kind = spec.get("kind", "bar")

        if kind == "line":
            return _render_line(d, spec, out_dir)
        if kind == "area":
            return _render_area(d, spec, out_dir)
        if kind in ("pie", "donut"):
            # 'donut' is a pie with a hole; honour either kind name.
            if kind == "donut" and not spec.get("hole"):
                spec = {**spec, "hole": 0.45}
            return _render_pie(d, spec, out_dir)
        return _render_bar(d, spec, out_dir)
    except Exception:
        return None, None


def _render_line(d, spec, out_dir):
    fig, ax = _new_axes()
    x, y, color = spec.get("x"), spec.get("y"), spec.get("color")
    order = spec.get("category_order")

    def _xvals(sub):
        xs = sub[x].astype(str)
        return xs

    if color and color in d.columns:                # multi-series: one line per group
        for i, (gname, sub) in enumerate(d.groupby(color, sort=False)):
            sub = sub.sort_values(spec.get("sort_by") or x) if (spec.get("sort_by") or x) in sub else sub
            ax.plot(_xvals(sub), sub[y], marker="o", markersize=4,
                    linewidth=2, label=_t(gname), color=_COLORWAY[i % len(_COLORWAY)])
        _brand_legend(ax, title=_t(color), fontsize=8, title_fontsize=9, loc="best", frameon=True)
    else:                                            # single (or multi-measure) line
        ycols = y if isinstance(y, list) else [y]
        for i, yc in enumerate(ycols):
            ax.plot(d[x].astype(str), d[yc], marker="o", markersize=4,
                    linewidth=2, label=_t(yc), color=_COLORWAY[i % len(_COLORWAY)])
        if len(ycols) > 1:
            _brand_legend(ax, fontsize=8, loc="best", frameon=True)

    if order:
        # matplotlib keeps categorical x in plotted order; ensure chronological via order list
        ax.set_xticks(range(len(order)))
        ax.set_xticklabels([_t(v) for v in order], rotation=30, ha="right", fontsize=9)
    else:
        for lbl in ax.get_xticklabels():
            lbl.set_rotation(30); lbl.set_ha("right"); lbl.set_fontsize(9)
    return _finish(fig, ax, spec, out_dir)


def _render_area(d, spec, out_dir):
    """Filled trend. Single series → one filled line; a grouping color column → STACKED area."""
    fig, ax = _new_axes()
    x, y, color = spec.get("x"), spec.get("y"), spec.get("color")
    order = spec.get("category_order")
    sort_by = spec.get("sort_by") or x

    if color and color in d.columns:                 # stacked area, one band per group
        piv = d.pivot_table(index=x, columns=color, values=y, aggfunc="sum", fill_value=0)
        if order:
            piv = piv.reindex([o for o in order if o in piv.index])
        elif sort_by in d.columns:
            piv = piv.reindex(d.sort_values(sort_by)[x].drop_duplicates())
        xs = [_t(v) for v in piv.index.astype(str)]
        series = [piv[c].values for c in piv.columns]
        ax.stackplot(xs, *series, labels=[_t(c) for c in piv.columns],
                     colors=[_COLORWAY[i % len(_COLORWAY)] for i in range(len(piv.columns))],
                     alpha=0.85)
        _brand_legend(ax, title=_t(color), fontsize=8, title_fontsize=9, loc="best", frameon=True)
    else:                                            # single filled series
        dd = d.sort_values(sort_by) if sort_by in d.columns else d
        xs = dd[x].astype(str).map(_t)
        ax.fill_between(range(len(dd)), dd[y], color=_COLORWAY[0], alpha=0.30, zorder=2)
        ax.plot(range(len(dd)), dd[y], color=_COLORWAY[0], linewidth=2, marker="o",
                markersize=4, zorder=3)
        ax.set_xticks(range(len(dd)))
        ax.set_xticklabels(list(xs), rotation=30, ha="right", fontsize=9)

    _thousands(ax, "y")
    for lbl in ax.get_xticklabels():
        lbl.set_rotation(30); lbl.set_ha("right"); lbl.set_fontsize(9)
    return _finish(fig, ax, spec, out_dir)


def _render_bar(d, spec, out_dir):
    x, y, color = spec.get("x"), spec.get("y"), spec.get("color")
    orient = spec.get("orientation", "v")

    # Wide-form grouped bars: y is a list of measure columns, x is the category.
    if isinstance(y, list):
        return _render_grouped_wide(d, spec, out_dir)

    # Grouped by a color column (long form): pivot to category × group.
    if color and color in d.columns and orient == "h":
        return _render_grouped_long(d, spec, out_dir)

    fig, ax = _new_axes()
    if orient == "h":
        cats = d[y].astype(str).map(_t)
        ax.barh(cats, d[x], color=_COLORWAY[0], zorder=3)
        _thousands(ax, axis="x")
    else:
        cats = d[x].astype(str).map(_t)
        ax.bar(cats, d[y], color=_COLORWAY[0], zorder=3)
        _thousands(ax, axis="y")
        for lbl in ax.get_xticklabels():
            lbl.set_rotation(30); lbl.set_ha("right"); lbl.set_fontsize(9)
    return _finish(fig, ax, spec, out_dir)


def _render_grouped_wide(d, spec, out_dir):
    """x = category column, y = list of measure columns → side-by-side bars."""
    import numpy as np
    fig, ax = _new_axes()
    x = spec.get("x")
    ycols = [c for c in spec["y"] if c in d.columns]
    cats = d[x].astype(str).map(_t).tolist()
    n = len(ycols)
    idx = np.arange(len(cats))
    width = 0.8 / max(n, 1)
    for i, yc in enumerate(ycols):
        ax.bar(idx + i * width, d[yc], width, label=_t(yc),
               color=_COLORWAY[i % len(_COLORWAY)], zorder=3)
    ax.set_xticks(idx + width * (n - 1) / 2)
    ax.set_xticklabels(cats, rotation=30, ha="right", fontsize=9)
    _brand_legend(ax, fontsize=8, loc="best", frameon=True)
    _thousands(ax, axis="y")
    return _finish(fig, ax, spec, out_dir)


def _render_grouped_long(d, spec, out_dir):
    """Long form: y=item, x=measure, color=group → grouped horizontal bars per group."""
    import numpy as np
    fig, ax = _new_axes()
    x, ycat, color = spec.get("x"), spec.get("y"), spec.get("color")
    groups = list(dict.fromkeys(d[color].astype(str)))
    items = list(dict.fromkeys(d[ycat].astype(str)))
    idx = np.arange(len(items))
    height = 0.8 / max(len(groups), 1)
    for gi, g in enumerate(groups):
        sub = d[d[color].astype(str) == g].set_index(d[ycat].astype(str))
        vals = [float(sub[x].get(it, 0)) for it in items]
        ax.barh(idx + gi * height, vals, height, label=_t(g),
                color=_COLORWAY[gi % len(_COLORWAY)], zorder=3)
    ax.set_yticks(idx + height * (len(groups) - 1) / 2)
    ax.set_yticklabels([_t(it) for it in items], fontsize=9)
    _brand_legend(ax, title=_t(color), fontsize=8, title_fontsize=9, loc="best", frameon=True)
    _thousands(ax, axis="x")
    return _finish(fig, ax, spec, out_dir)


def _render_pie(d, spec, out_dir):
    fig, ax = plt.subplots(figsize=(_FIG_W_IN, _FIG_H_IN))
    fig.patch.set_facecolor(_PAPER_BG)
    names = spec.get("names", spec.get("x"))
    values = spec.get("values", spec.get("y"))
    labels = d[names].astype(str).map(_t).tolist()
    hole = spec.get("hole", 0)
    wedgeprops = dict(width=0.45) if hole else None
    # Hide labels for tiny slices to avoid clutter; keep % on all.
    ax.pie(
        d[values], labels=labels, autopct="%1.1f%%", startangle=90,
        colors=[_COLORWAY[i % len(_COLORWAY)] for i in range(len(d))],
        textprops={"fontsize": 8, "color": _FONT_COLOR},
        wedgeprops=wedgeprops, pctdistance=0.8,
    )
    ax.axis("equal")
    if spec.get("title"):
        ax.set_title(_t(spec["title"]), fontsize=_TITLE_SIZE, color=_TITLE_COLOR, pad=12)
    png = _fig_to_png(fig)
    return png, _save(png, out_dir)


def _thousands(ax, axis="y"):
    import matplotlib.ticker as mticker

    def fmt(v, _pos):
        av = abs(v)
        if av >= 1_000_000:
            return f"{v/1_000_000:.1f}M"
        if av >= 1_000:
            return f"{v/1_000:.0f}K"
        return f"{v:.0f}"

    target = ax.yaxis if axis == "y" else ax.xaxis
    target.set_major_formatter(mticker.FuncFormatter(fmt))


# ── Compound subplot (display_separately) ─────────────────────
_TIME_KWS = ("month", "year", "date", "week", "day", "quarter")


def render_compound(step_results, step_labels, out_dir: str | None = None):
    """One panel per step result — line for a time-series step, hbar for a categorical step.
    Mirrors the old Plotly _build_compound_chart. Returns (png, path) or (None, None)."""
    try:
        chartable = []
        for df, label in zip(step_results, step_labels):
            if df is None or getattr(df, "empty", True) or len(df) <= 1:
                continue
            num = _measure_cols(df)          # measures only (no year/month/id)
            if not num:
                continue
            # Prefer a readable time label for the x-axis; else a date/numeric time part.
            time_x = (
                next((c for c in df.columns if c.lower().endswith(("month_name", "day_name"))), None)
                or next((c for c in df.columns
                         if c.lower().endswith(("month", "quarter", "week", "day")) and _is_time_part_col(c)), None)
                or next((c for c in df.columns if "date" in c.lower()), None)
            )
            cat = [c for c in df.columns
                   if c not in df.select_dtypes(include="number").columns and not _is_time_part_col(c)
                   and c != time_x]
            chartable.append({
                "df": df, "label": str(label)[:55],
                "num": num[0], "cat": cat[0] if cat else None,
                "time": time_x,
            })
        if not chartable:
            return None, None

        n = len(chartable)
        fig, axes = plt.subplots(1, n, figsize=(_FIG_W_IN * min(n, 2), _FIG_H_IN))
        if n == 1:
            axes = [axes]
        for ax, c in zip(axes, chartable):
            ax.set_facecolor("white")
            ax.grid(True, color="#e9e9ef", linewidth=0.8, zorder=0)
            ax.set_axisbelow(True)
            for sp in ("top", "right"):
                ax.spines[sp].set_visible(False)
            d = c["df"]
            if c["time"]:
                dd = d.sort_values(c["time"])
                ax.plot(dd[c["time"]].astype(str), dd[c["num"]], marker="o",
                        markersize=4, linewidth=2, color=_COLORWAY[0])
                for lbl in ax.get_xticklabels():
                    lbl.set_rotation(30); lbl.set_ha("right"); lbl.set_fontsize(8)
                _thousands(ax, "y")
            elif c["cat"]:
                dd = d.sort_values(c["num"])
                ax.barh(dd[c["cat"]].astype(str).map(_t), dd[c["num"]],
                        color=_COLORWAY[0], zorder=3)
                _thousands(ax, "x")
            ax.set_title(_t(c["label"]), fontsize=11, pad=8)
        fig.tight_layout()
        png = _fig_to_png(fig)
        return png, _save(png, out_dir)
    except Exception:
        return None, None


# ── Heuristic fallback (no spec, no LLM) ──────────────────────
def render_auto(df, intent: dict | None = None, question: str = "", out_dir: str | None = None):
    """Best-effort static chart for a result that produced no spec — inferred from the
    data shape alone (NO LLM, NO code execution). Returns (png, path) or (None, None)."""
    try:
        intent = intent or {}
        if df is None or len(df) < 2:
            return None, None

        num = _measure_cols(df)               # measures only (no year/month/id)
        txt = [c for c in df.columns if c not in df.select_dtypes(include="number").columns]
        # Any column that denotes time — a readable name column, a date, or a numeric part.
        time_any = [c for c in df.columns
                    if _is_time_part_col(c) or any(k in str(c).lower() for k in ("date", "month_name", "day_name"))]
        if not num:
            return None, None

        # Time series → line. Prefer a readable label (month_name) for x, else the finest
        # numeric time part (month/quarter/week/day), else a date column.
        if time_any and len(df) > 2:
            x_col = (
                next((c for c in txt if c.lower().endswith(("month_name", "day_name"))), None)
                or next((c for c in df.columns if c.lower().endswith(("month", "quarter", "week", "day"))
                         and _is_time_part_col(c)), None)
                or next((c for c in df.columns if "date" in c.lower()), None)
                or time_any[0]
            )
            order_col = next((c for c in df.columns
                              if c.lower().endswith(("month", "quarter", "week", "day")) and _is_time_part_col(c)), None)
            # a grouping categorical that is NOT the time label → multi-line
            group = next((c for c in txt if c != x_col and not c.lower().endswith(("month_name", "day_name"))), None)
            spec = {"kind": "line", "x": x_col, "y": num[0], "color": group,
                    "sort_by": order_col, "title": question}
            return render_spec(df, spec, out_dir)

        # Categorical + one measure → hbar
        cat = next((c for c in txt if not _is_time_part_col(c)), None)
        if cat:
            spec = {"kind": "bar", "x": num[0], "y": cat, "orientation": "h",
                    "sort_by": num[0], "ascending": True, "title": question}
            return render_spec(df, spec, out_dir)
        return None, None
    except Exception:
        return None, None
