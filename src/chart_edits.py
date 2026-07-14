"""
chart_edits.py
==============
Deterministic chart-edit handlers.

The LLM is unreliable at Plotly transforms (e.g. it sets orientation='v' but
forgets to swap the x/y arrays, producing an empty chart). So the common,
well-defined edits are applied here with correct Plotly operations — no LLM
guesswork. The pipeline falls back to the LLM only for genuinely freeform edits
this module doesn't recognize.

Public API:
    apply_deterministic_edit(fig, instruction) -> (new_fig | None, label)
        Returns a NEW edited figure and a short label, or (None, "") if the
        instruction isn't a recognized deterministic edit.
"""
from __future__ import annotations

import copy
import re
from typing import Optional, Tuple

import plotly.graph_objects as go

from src.config import NAMAA_COLORWAY

# Brand colours available by name (Arabic + English keywords → hex).
_COLOR_WORDS = {
    # green
    "green": "#059669", "أخضر": "#059669", "اخضر": "#059669", "الأخضر": "#059669",
    # red
    "red": "#d4183d", "أحمر": "#d4183d", "احمر": "#d4183d", "الأحمر": "#d4183d",
    # blue
    "blue": "#1e40af", "أزرق": "#1e40af", "ازرق": "#1e40af", "الأزرق": "#1e40af",
    # orange
    "orange": "#f59e0b", "برتقالي": "#f59e0b", "البرتقالي": "#f59e0b",
    # purple / indigo (brand)
    "purple": "#3D1B6A", "indigo": "#3D1B6A", "بنفسجي": "#3D1B6A", "أرجواني": "#3D1B6A",
    "بنفسجى": "#3D1B6A",
    # yellow
    "yellow": "#eab308", "أصفر": "#eab308", "اصفر": "#eab308",
    # teal / cyan
    "teal": "#06B6D4", "cyan": "#06B6D4", "سماوي": "#06B6D4", "تركوازي": "#06B6D4",
    # gray
    "gray": "#717182", "grey": "#717182", "رمادي": "#717182", "رمادى": "#717182",
    # black / white
    "black": "#111111", "أسود": "#111111", "اسود": "#111111",
    "white": "#ffffff", "أبيض": "#ffffff", "ابيض": "#ffffff",
}


def _has(text: str, *words: str) -> bool:
    t = text.lower()
    return any(w.lower() in t for w in words)


def _arr(v):
    """Decode a Plotly data array to a plain Python list.

    Figures round-tripped through JSON store numeric arrays as base64 typed
    arrays ({'dtype':..,'bdata':..}); reading tr.x/tr.y then yields that dict
    instead of the numbers. This decodes both the typed-array form and normal
    sequences to a flat list."""
    if v is None:
        return []
    # Typed-array dict form
    if isinstance(v, dict) and "bdata" in v:
        import base64
        import numpy as _np
        dtype = v.get("dtype", "f8")
        raw = base64.b64decode(v["bdata"])
        return _np.frombuffer(raw, dtype=dtype).tolist()
    try:
        return list(v)
    except TypeError:
        return [v]


def _first_bar(fig: go.Figure):
    for tr in fig.data:
        if tr.type == "bar":
            return tr
    return fig.data[0] if fig.data else None


def _swap_orientation(fig: go.Figure, target: str) -> bool:
    """Flip bar orientation to 'h' or 'v' (or toggle if target is None),
    swapping x<->y data so the bars actually render. Returns True if changed."""
    changed = False
    for tr in fig.data:
        if tr.type != "bar":
            continue
        cur = tr.orientation or ("h" if (tr.y is not None and tr.x is not None
                                         and _looks_categorical(tr.y)) else "v")
        new = target or ("v" if cur == "h" else "h")
        if new != cur:
            tr.x, tr.y = tr.y, tr.x
            tr.orientation = new
            changed = True
        elif tr.orientation != new:
            tr.orientation = new
            changed = True
    if changed:
        # swap axis titles too
        xt = fig.layout.xaxis.title.text if fig.layout.xaxis and fig.layout.xaxis.title else None
        yt = fig.layout.yaxis.title.text if fig.layout.yaxis and fig.layout.yaxis.title else None
        fig.update_layout(xaxis_title=yt, yaxis_title=xt)
    return changed


def _looks_categorical(vals) -> bool:
    seq = _arr(vals)
    if not seq:
        return False
    try:
        for v in seq[:5]:
            float(v)
        return False
    except (TypeError, ValueError):
        return True


def apply_deterministic_edit(
    fig: go.Figure, instruction: str
) -> Tuple[Optional[go.Figure], str]:
    """
    Apply a recognized deterministic edit to a COPY of `fig`.
    Returns (edited_fig, label) or (None, "") if unrecognized → LLM fallback.
    """
    if fig is None or not fig.data:
        return None, ""
    ins = instruction.strip()
    low = ins.lower()
    f = copy.deepcopy(fig)

    # ── Reverse an axis (checked BEFORE flip: "اعكس المحور" is axis-reverse,
    #    not an orientation flip) ────────────────────────────────────
    if _has(low, "reverse", "اعكس", "عكس ترتيب", "عكس") and _has(
        low, "axis", "محور", "الأفقي", "الافقي", "المحور", " x", "x-axis", "x axis"
    ):
        f.update_layout(xaxis=dict(autorange="reversed"))
        return f, "x-axis reversed"

    # ── Orientation: flip / horizontal / vertical ─────────────────
    to_h = _has(low, "horizontal", "hbar", "أفقي", "افقي", "افقياً", "أفقياً", "افقيا")
    to_v = _has(low, "vertical", "عمودي", "عموديا", "عمودياً", "رأسي", "راسي")
    is_flip = _has(low, "flip", "اقلب", "اعكس الاتجاه", "قلب")
    if to_h or to_v or is_flip:
        target = "h" if to_h else ("v" if to_v else None)
        if _swap_orientation(f, target):
            return f, f"orientation→{target or 'toggled'}"
        # already in that orientation — still return the fig unchanged as success
        return f, "orientation (no change needed)"

    # ── Convert chart TYPE: pie / area / line ─────────────────────
    if _has(low, "pie", "دائري", "دائرة", "قطاعي", "فطيرة"):
        return _to_pie(f), "→pie"
    if _has(low, "area", "مساحي", "مساحة"):
        return _set_type(f, "scatter", fill="tozeroy", mode="lines"), "→area"
    if _has(low, "line chart", "خط بياني", "مخطط خطي", "خطي") and not _has(low, "trend"):
        return _set_type(f, "scatter", mode="lines+markers"), "→line"
    if _has(low, "markers only", "نقاط فقط", "علامات فقط", "data points as markers"):
        for tr in f.data:
            if tr.type == "scatter":
                tr.mode = "markers"
        return f, "markers-only"

    # ── Colour change ─────────────────────────────────────────────
    if _has(low, "color", "colour", "لون", "ألوان", "الوان"):
        hexc = None
        for word, hx in _COLOR_WORDS.items():
            if word.lower() in low:
                hexc = hx
                break
        if hexc:
            for tr in f.data:
                if tr.type == "bar":
                    tr.marker.color = hexc
                elif tr.type == "scatter":
                    tr.line.color = hexc
                    tr.marker.color = hexc
                elif tr.type == "pie":
                    tr.marker.colors = [hexc] * (len(tr.labels) if tr.labels else 1)
            return f, f"color→{hexc}"
        # colour requested but no known colour word → let LLM try
        return None, ""

    # ── Title ─────────────────────────────────────────────────────
    if _has(low, "title", "عنوان", "عنواناً", "عنوانا"):
        title = _extract_title(ins)
        if title:
            f.update_layout(title_text=title)
            return f, "title set"
        return None, ""

    # ── Sorting ───────────────────────────────────────────────────
    if _has(low, "sort", "رتب", "ترتيب", "ascending", "descending", "تصاعدي", "تنازلي"):
        asc = _has(low, "ascending", "تصاعدي", "تصاعديا", "تصاعدياً")
        desc = _has(low, "descending", "تنازلي", "تنازليا", "تنازلياً")
        if not asc and not desc:
            desc = True  # default: highest first
        if _sort_bars(f, ascending=asc and not desc):
            return f, f"sorted {'asc' if asc and not desc else 'desc'}"
        return None, ""

    # ── Theme / background ────────────────────────────────────────
    if _has(low, "dark", "داكن", "داكنة", "غامق", "أسود الخلفية"):
        f.update_layout(
            paper_bgcolor="#1a1a2e", plot_bgcolor="#1a1a2e",
            font_color="#f0f0f0",
            xaxis=dict(gridcolor="rgba(255,255,255,0.1)"),
            yaxis=dict(gridcolor="rgba(255,255,255,0.1)"),
        )
        return f, "dark theme"
    if (_has(low, "background", "خلفية") and _has(low, "white", "light", "بيضاء", "أبيض", "ابيض", "فاتحة", "فاتح")):
        f.update_layout(
            paper_bgcolor="#ffffff", plot_bgcolor="#ffffff",
            font_color="#030213",
            xaxis=dict(gridcolor="rgba(0,0,0,0.05)"),
            yaxis=dict(gridcolor="rgba(0,0,0,0.05)"),
        )
        return f, "white background"

    # Not a recognized deterministic edit → caller falls back to the LLM.
    return None, ""


def _set_type(fig: go.Figure, new_type: str, **kwargs) -> go.Figure:
    """Return a NEW figure with traces converted to another type, preserving x/y
    and the original layout. (Plotly forbids reassigning fig.data to different
    trace types in place, so we rebuild.)"""
    new_traces = []
    for tr in fig.data:
        x, y, name = _arr(tr.x), _arr(tr.y), tr.name
        if new_type == "scatter":
            new_traces.append(go.Scatter(x=x, y=y, name=name, **kwargs))
        else:
            new_traces.append(go.Bar(x=x, y=y, name=name))
    nf = go.Figure(data=new_traces, layout=fig.layout)
    return nf


def _to_pie(fig: go.Figure) -> go.Figure:
    """Build a pie chart from the first trace's category/value pair."""
    tr = _first_bar(fig)
    if tr is None:
        return fig
    x, y = _arr(tr.x), _arr(tr.y)
    # labels = categorical axis, values = numeric axis
    if _looks_categorical(tr.x):
        labels, values = x, y
    else:
        labels, values = y, x
    title = fig.layout.title.text if fig.layout.title else None
    pie = go.Figure(go.Pie(labels=labels, values=values, hole=0))
    pie.update_layout(title_text=title, colorway=NAMAA_COLORWAY)
    return pie


def _sort_bars(fig: go.Figure, ascending: bool) -> bool:
    """Sort a bar trace by its numeric values."""
    tr = _first_bar(fig)
    if tr is None or tr.x is None or tr.y is None:
        return False
    x, y = _arr(tr.x), _arr(tr.y)
    # figure out which axis is numeric
    if _looks_categorical(tr.x):
        pairs = sorted(zip(x, y), key=lambda p: p[1], reverse=not ascending)
        tr.x, tr.y = [p[0] for p in pairs], [p[1] for p in pairs]
    else:
        pairs = sorted(zip(y, x), key=lambda p: p[1], reverse=not ascending)
        tr.y, tr.x = [p[0] for p in pairs], [p[1] for p in pairs]
    return True


def _extract_title(instruction: str) -> Optional[str]:
    """Pull the desired title out of the instruction after ':' or key phrases."""
    # After a colon (Arabic or Latin)
    m = re.search(r"[:：]\s*(.+)$", instruction)
    if m:
        return m.group(1).strip().strip('"“”\'')
    # 'add title X' / 'عنوان X'
    m = re.search(r"(?:title|عنوان[اً]?)\s+(.+)$", instruction, re.IGNORECASE)
    if m:
        return m.group(1).strip().strip('"“”\'')
    return None
