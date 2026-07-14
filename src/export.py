"""
export.py
=========
PDF report generation, business recommendations, summary fallback,
and follow-up question generation.
"""

from __future__ import annotations

import datetime
import json
import os
import re
from typing import List

import pandas as pd
from langchain_core.output_parsers import StrOutputParser

from src.config import BASE_DIR
from src.llm import llm
from src.prompts import (
    BUSINESS_RECO_PROMPT,
    FOLLOWUP_PROMPT,
    SUMMARY_PROMPT,
    NL_SUMMARY_PROMPT,
)
from src import session as _sess
from utils.arabic import (
    _ar_str, _AR_FONT, _AR_FONT_B, _LAT_FONT, _LAT_FONT_B,
    has_arabic, rl_alignment, font_for,
)
from utils.formatting import _format_number_cols

# ── reportlab availability ─────────────────────────────────────
try:
    from reportlab.lib.pagesizes import A4 as _RL_A4
    from reportlab.platypus import (
        SimpleDocTemplate as _RL_Doc,
        Paragraph as _RL_P,
        Spacer as _RL_Sp,
        Table as _RL_T,
        TableStyle as _RL_TS,
        Image as _RL_Image,
        HRFlowable as _RL_HR,
        PageBreak as _RL_PB,
    )
    from reportlab.lib.styles import (
        getSampleStyleSheet as _rl_styles,
        ParagraphStyle as _RL_PS,
    )
    from reportlab.lib import colors as _RL_COLORS
    from reportlab.lib.units import cm as _RL_CM

    _REPORTLAB_OK = True
except ImportError:
    _REPORTLAB_OK = False


# ── Summary fallback ───────────────────────────────────────────
def _generate_summary_fallback(
    question: str,
    error: str,
    schema_context: str = "",
) -> str:
    """LLM text-only answer when pandas code fails all retries."""
    try:
        return (
            (SUMMARY_PROMPT | llm | StrOutputParser())
            .invoke(
                {
                    "question": question,
                    "error": error,
                    "schema_hint": (
                        schema_context[:600] if schema_context else "No schema."
                    ),
                }
            )
            .strip()
        )
    except Exception as e:
        return f"\u26a0\ufe0f تعذّر تنفيذ الاستعلام. يرجى إعادة الصياغة. ({e})"


# ══════════════════════════════════════════════════════════════
# Async LLM-driven generators — recommendations, follow-ups, summary stream
# These replace the deleted sync versions; only the async pipeline calls them.
# ══════════════════════════════════════════════════════════════

async def _generate_business_recommendation_async(
    question: str,
    result: pd.DataFrame,
    intent_type: str,
) -> str:
    from src.session import _build_recommendations_context

    preview = result.head(30).to_string(index=False)
    if len(result) > 30:
        preview += f"\n... ({len(result) - 30} more rows)"
    try:
        out = await (BUSINESS_RECO_PROMPT | llm | StrOutputParser()).ainvoke({
            "question": question,
            "data_preview": preview,
            "columns": list(result.columns),
            "intent_type": intent_type,
            "accumulated_recommendations": _build_recommendations_context(),
        })
        return out.strip()
    except Exception as e:
        return f"⚠️ Could not generate recommendations: {e}"


async def _generate_followup_questions_async(
    question: str,
    result: pd.DataFrame,
) -> List[str]:
    try:
        preview = result.head(10).to_string(index=False)
        raw = await (FOLLOWUP_PROMPT | llm | StrOutputParser()).ainvoke({
            "question": question,
            "result_preview": preview,
            "columns": list(result.columns),
        })
        raw = re.sub(r"```(?:json)?\s*", "", raw.strip()).replace("```", "").strip()
        qs = json.loads(raw)
        return qs if isinstance(qs, list) else []
    except Exception:
        return []


async def _generate_nl_summary_stream_async(
    question: str, result: pd.DataFrame, preview_override: str = None
):
    """Async streamed plain-language interpretation of the query result."""
    try:
        if preview_override:
            preview = preview_override
        else:
            preview = result.head(20).to_string(index=False)
            if len(result) > 20:
                preview += f"\n... ({len(result) - 20} more rows)"
        from src.prompts import NL_SUMMARY_PROMPT

        chain = NL_SUMMARY_PROMPT | llm | StrOutputParser()
        async for chunk in chain.astream({
            "question": question,
            "data_preview": preview,
            "columns": list(result.columns),
        }):
            yield chunk
    except Exception as e:
        yield f"⚠️ Summary unavailable: {e}"


# ── PDF helpers ────────────────────────────────────────────────
def _format_kpi_value(val, col_name: str) -> str:
    """Format a KPI value with K/M abbreviation + KWD suffix when column looks monetary."""
    numeric = isinstance(val, (int, float)) and not isinstance(val, bool)
    if not numeric:
        return str(val)
    is_money = any(
        k in col_name.lower()
        for k in ["revenue", "price", "fee", "total", "sales", "_kwd", "_jd", "amount", "value"]
    )
    av = abs(val)
    if av >= 1_000_000:
        return f"{val / 1_000_000:.2f}M KWD" if is_money else f"{val / 1_000_000:.2f}M"
    if av >= 1_000:
        return f"{val / 1_000:.1f}K KWD" if is_money else f"{val / 1_000:.1f}K"
    return (
        f"{round(val, 2)} KWD" if is_money
        else (f"{val:,}" if isinstance(val, int) else f"{round(val, 2)}")
    )


def _render_kpi_block(kpi_data: dict, body_style) -> list:
    """
    Render a single-row result as a styled KPI paragraph (ReportLab only — no rasterization).
    Used in the PDF when a query has no chart_path. Returns a list of flowables.
    """
    if not kpi_data:
        return []

    # KPI labels are column names (English) and values are numeric → use the
    # Latin font so nothing drops. `fl`/`flb` name the Latin regular/bold fonts.
    fl, flb = _LAT_FONT, _LAT_FONT_B
    if len(kpi_data) == 1:
        col, val = next(iter(kpi_data.items()))
        formatted = _format_kpi_value(val, col)
        para = _RL_P(
            f"<para alignment='center' spaceb='4' spacea='4'>"
            f"<font name='{flb}' size='28' color='#1a1a2e'><b>{_esc(formatted)}</b></font><br/>"
            f"<font name='{fl}' size='10' color='#717182'>{_esc(col.upper())}</font>"
            f"</para>",
            body_style,
        )
        return [_RL_Sp(1, 0.3 * _RL_CM), para, _RL_Sp(1, 0.4 * _RL_CM)]

    # Multi-KPI: small centered table with label/value rows
    rows = []
    for col, val in kpi_data.items():
        rows.append([
            _RL_P(f"<font name='{fl}' size='9' color='#717182'>{_esc(col.upper())}</font>", body_style),
            _RL_P(
                f"<font name='{flb}' size='14' color='#1a1a2e'><b>{_esc(_format_kpi_value(val, col))}</b></font>",
                body_style,
            ),
        ])
    tbl = _RL_T(rows, colWidths=[6 * _RL_CM, 6 * _RL_CM], hAlign="CENTER")
    tbl.setStyle(
        _RL_TS([
            ("BACKGROUND", (0, 0), (-1, -1), _RL_COLORS.HexColor("#f8fafc")),
            ("BOX", (0, 0), (-1, -1), 0.4, _RL_COLORS.HexColor("#cbd5e1")),
            ("INNERGRID", (0, 0), (-1, -1), 0.2, _RL_COLORS.HexColor("#e2e8f0")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("LEFTPADDING", (0, 0), (-1, -1), 8),
            ("RIGHTPADDING", (0, 0), (-1, -1), 8),
            ("TOPPADDING", (0, 0), (-1, -1), 6),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
        ])
    )
    return [_RL_Sp(1, 0.3 * _RL_CM), tbl, _RL_Sp(1, 0.4 * _RL_CM)]


def _esc(text: str) -> str:
    """Escape XML special chars so reportlab's mini-markup never breaks."""
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _wrap_arabic_lines(text: str, max_chars: int = 95) -> list:
    """
    Manually split text into short lines (≤ max_chars).
    For Arabic, BiDi is applied AFTER splitting so reportlab never re-wraps a
    BiDi-reversed string (which would corrupt the visual order). English lines
    are returned as-is (reportlab wraps LTR text fine, but pre-splitting keeps
    both languages on the same code path).
    """
    result = []
    for natural in text.split("\n"):
        natural = natural.strip()
        if not natural:
            continue
        words = natural.split()
        current: list = []
        length = 0
        for word in words:
            wl = len(word)
            if length + wl + 1 > max_chars and current:
                result.append(" ".join(current))
                current = [word]
                length = wl
            else:
                current.append(word)
                length += wl + 1
        if current:
            result.append(" ".join(current))
    return result


# Characters the Arabic font renders (Arabic letters, digits, spaces, comma/period).
# Everything else (= + ( ) / * % - and Latin letters) has NO glyph in the Arabic
# font and must be drawn with the Latin font, or it silently disappears.
_AR_RENDERABLE = re.compile(r"[؀-ۿﭐ-﻿0-9\s.,،؛؟]")


def _mixed_markup(rendered: str, latin_font: str) -> str:
    """
    Given an already-reshaped/BiDi string, wrap every run of characters the Arabic
    font can't draw (Latin letters, % = + ( ) / * - …) in a <font name="Latin">
    tag so they render instead of vanishing. Arabic runs are left in the base font.
    Input is escaped here (do NOT pre-escape).
    """
    out = []
    buf = []
    buf_latin = None  # None until first char decides the run type

    def flush():
        if not buf:
            return
        seg = _esc("".join(buf))
        if buf_latin:
            out.append(f'<font name="{latin_font}">{seg}</font>')
        else:
            out.append(seg)

    for ch in rendered:
        is_latin = not bool(_AR_RENDERABLE.match(ch))
        if buf_latin is None:
            buf_latin = is_latin
        if is_latin != buf_latin:
            flush()
            buf = []
            buf_latin = is_latin
        buf.append(ch)
    flush()
    return "".join(out)


def _para(text: str, base_style, *, bold: bool = False):
    """
    Build a language-aware reportlab Paragraph:
      - Arabic  → reshape+BiDi, right-align, Arabic font, with ASCII/symbol runs
                  drawn in the Latin font (so % = ( ) - / * and Latin words show).
      - English → untouched, left-align, Latin font.
    """
    align = rl_alignment(text)
    is_ar = has_arabic(text)
    st = _RL_PS(
        base_style.name + ("_r" if align == 2 else "_l") + ("_b" if bold else ""),
        parent=base_style,
        alignment=align,
        fontName=font_for(text, bold=bold),  # Arabic vs Latin base font
    )
    rendered = _ar_str(text)
    try:
        if is_ar:
            latin = _LAT_FONT_B if bold else _LAT_FONT
            markup = _mixed_markup(rendered, latin)
            return _RL_P(markup, st)
        return _RL_P(_esc(rendered), st)
    except Exception:
        return _RL_P(_esc(str(text)), st)


def _reco_paragraphs(text: str, num_style, body_style) -> list:
    """
    Render a recommendation string as reportlab elements — bilingual-safe.
    - Strip **bold** markers (inline bold can't mix with manual BiDi wrap).
    - Split into numbered items; each item = bold "N." heading + wrapped body.
    - Every line is rendered via _para() so Arabic and English each get the
      correct shaping + alignment.
    """
    paras = []
    plain = re.sub(r"\*\*(.*?)\*\*", r"\1", text)

    items = re.split(r"(?m)(?=^\d+[\.\)])", plain.strip())
    for item in items:
        item = item.strip()
        if not item:
            continue

        m = re.match(r"^(\d+[\.\)])\s*(.*)", item, re.DOTALL)
        if m:
            num = m.group(1)
            body = m.group(2).strip()
            # Number heading follows the body's language for alignment.
            head_align = rl_alignment(body)
            nst = _RL_PS(
                num_style.name + ("_r" if head_align == 2 else "_l"),
                parent=num_style, alignment=head_align,
                fontName=font_for(body, bold=True),
            )
            paras.append(_RL_P(f"<b>{_esc(num)}</b>", nst))
            for wrapped in _wrap_arabic_lines(body):
                if wrapped:
                    paras.append(_para(wrapped, body_style))
        else:
            for wrapped in _wrap_arabic_lines(item):
                if wrapped:
                    paras.append(_para(wrapped, body_style))

        paras.append(_RL_Sp(1, 0.25 * _RL_CM))
    return paras


# ── PDF report generator ───────────────────────────────────────
def _generate_pdf_report() -> str:
    if not _REPORTLAB_OK:
        return "\u274c reportlab not installed. Run: pip install reportlab"

    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    PDF_PATH = os.path.join(BASE_DIR, f"namaa_report_{timestamp}.pdf")

    try:
        page_w, page_h = _RL_A4
        doc = _RL_Doc(
            PDF_PATH,
            pagesize=_RL_A4,
            rightMargin=2 * _RL_CM,
            leftMargin=2 * _RL_CM,
            topMargin=2 * _RL_CM,
            bottomMargin=2 * _RL_CM,
        )

        # ── Styles ────────────────────────────────────────────
        INDIGO = _RL_COLORS.HexColor("#3D1B6A")
        ACCENT = _RL_COLORS.HexColor("#06B6D4")
        INK = _RL_COLORS.HexColor("#1a1a2e")
        MUTED = _RL_COLORS.HexColor("#717182")
        CARD_BG = _RL_COLORS.HexColor("#f7f6fb")

        # Alignment is set per-paragraph by _para() / _reco_paragraphs().
        # Title/subtitle are English → Latin font (the Arabic font has no Latin glyphs).
        title_s = _RL_PS(
            "DTitle", fontSize=20, fontName=_LAT_FONT_B, alignment=1,
            textColor=_RL_COLORS.white, leading=24,
        )
        sub_s = _RL_PS(
            "DSub", fontSize=9.5, fontName=_LAT_FONT, alignment=1,
            textColor=_RL_COLORS.HexColor("#d9ccec"), leading=13,
        )
        heading_s = _RL_PS(
            "DHead", fontSize=13, fontName=_LAT_FONT_B, spaceBefore=6, spaceAfter=8,
            textColor=INDIGO, alignment=0,
        )
        qtitle_s = _RL_PS(
            "DQTitle", fontSize=11, fontName=_AR_FONT_B, spaceAfter=0,
            textColor=_RL_COLORS.white, leading=15,
        )
        section_s = _RL_PS(
            "DSect", fontSize=9, fontName=_LAT_FONT_B, spaceBefore=6, spaceAfter=3,
            textColor=ACCENT, alignment=0,
        )
        body_s = _RL_PS(
            "DBody", fontSize=10, fontName=_AR_FONT, spaceAfter=4, leading=17,
            alignment=0, textColor=INK,
        )
        num_s = _RL_PS(
            "DNum", fontSize=10, fontName=_AR_FONT_B, spaceBefore=6, spaceAfter=2,
            leading=16, alignment=0, textColor=INDIGO,
        )

        avail_w = page_w - 4 * _RL_CM
        elems = []

        # ── Header ────────────────────────────────────────────
        header_tbl = _RL_T(
            [[_RL_P(_ar_str("NAMAA Analytics Agent \u2014 Session Report"), title_s)],
             [_RL_P(datetime.datetime.now().strftime("Generated %Y-%m-%d  %H:%M"), sub_s)]],
            colWidths=[avail_w],
        )
        header_tbl.setStyle(_RL_TS([
            ("BACKGROUND", (0, 0), (-1, -1), INDIGO),
            ("LINEBELOW", (0, -1), (-1, -1), 3, ACCENT),
            ("TOPPADDING", (0, 0), (-1, 0), 14),
            ("BOTTOMPADDING", (0, -1), (-1, -1), 12),
            ("LEFTPADDING", (0, 0), (-1, -1), 12),
            ("RIGHTPADDING", (0, 0), (-1, -1), 12),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ]))
        elems.append(header_tbl)
        elems.append(_RL_Sp(1, 0.5 * _RL_CM))

        # ── Query History ─────────────────────────────────────
        if _sess._query_history:
            elems.append(_RL_P("Query History", heading_s))
            hcell = _RL_PS("hcell", fontName=_LAT_FONT, fontSize=8.5, leading=11,
                           textColor=INK)
            hcell_c = _RL_PS("hcell_c", parent=hcell, alignment=1,
                             fontName=_LAT_FONT_B)
            hist_data = [[
                _RL_P("<b>#</b>", hcell_c), _RL_P("<b>Time</b>", hcell_c),
                _RL_P("<b>Question</b>", hcell_c), _RL_P("<b>Shape</b>", hcell_c),
            ]]
            for i, item in enumerate(_sess._query_history[-10:], 1):
                q = item["question"]
                qcell = _RL_PS("q%d" % i, parent=hcell, alignment=rl_alignment(q),
                               fontName=font_for(q))
                hist_data.append([
                    _RL_P(str(i), hcell_c),
                    _RL_P(_esc(item["timestamp"]), hcell_c),
                    _RL_P(_esc(_ar_str(q)), qcell),
                    _RL_P(_esc(item["shape"]), hcell_c),
                ])
            ht = _RL_T(
                hist_data,
                colWidths=[0.8 * _RL_CM, 2.2 * _RL_CM, None, 2.4 * _RL_CM],
                repeatRows=1, hAlign="CENTER",
            )
            ht.setStyle(_RL_TS([
                ("BACKGROUND", (0, 0), (-1, 0), INDIGO),
                ("TEXTCOLOR", (0, 0), (-1, 0), _RL_COLORS.white),
                ("FONTNAME", (0, 0), (-1, 0), _AR_FONT_B),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LINEBELOW", (0, 0), (-1, 0), 1, ACCENT),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [_RL_COLORS.white, CARD_BG]),
                ("LINEBELOW", (0, 1), (-1, -1), 0.25, _RL_COLORS.HexColor("#e5e5ec")),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ]))
            elems.append(ht)
            elems.append(_RL_Sp(1, 0.6 * _RL_CM))

        # ── Charts + Recommendations (one card per query) ─────
        if _sess._accumulated_recommendations:
            elems.append(_RL_P("Analysis Results & Business Recommendations", heading_s))
            elems.append(_RL_Sp(1, 0.2 * _RL_CM))

            for idx, rec in enumerate(_sess._accumulated_recommendations, 1):
                if not isinstance(rec, dict):
                    continue

                question_text = rec.get("question", "")
                reco_text = rec.get("recommendation", "")
                chart_path = rec.get("chart_path")
                kpi_data = rec.get("kpi_data")

                # Query title bar — indigo pill spanning the content width
                qts = _RL_PS("qt%d" % idx, parent=qtitle_s,
                             alignment=rl_alignment(question_text),
                             fontName=font_for(question_text, bold=True))
                title_bar = _RL_T(
                    [[_RL_P(_esc(_ar_str(f"{idx}.  {question_text}")), qts)]],
                    colWidths=[avail_w],
                )
                title_bar.setStyle(_RL_TS([
                    ("BACKGROUND", (0, 0), (-1, -1), INDIGO),
                    ("TOPPADDING", (0, 0), (-1, -1), 7),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                    ("LEFTPADDING", (0, 0), (-1, -1), 10),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                ]))
                elems.append(title_bar)
                elems.append(_RL_Sp(1, 0.3 * _RL_CM))

                # Chart image (if available), else KPI block for single-row results
                if chart_path and os.path.exists(chart_path):
                    try:
                        img = _RL_Image(
                            chart_path, width=avail_w, height=avail_w * 460 / 900
                        )
                        img.hAlign = "CENTER"
                        elems.append(img)
                        elems.append(_RL_Sp(1, 0.3 * _RL_CM))
                    except Exception:
                        pass
                elif kpi_data:
                    # Single-row result with no chart → styled KPI block in lieu of an image
                    elems.extend(_render_kpi_block(kpi_data, body_s))

                # Recommendation text, under a cyan section label
                if reco_text:
                    elems.append(_RL_P("Insights &amp; Recommendations", section_s))
                    elems.extend(_reco_paragraphs(reco_text, num_s, body_s))

                # Card divider between queries
                elems.append(_RL_Sp(1, 0.2 * _RL_CM))
                elems.append(_RL_HR(width="100%", thickness=0.5,
                                    color=_RL_COLORS.HexColor("#e5e5ec"), spaceAfter=2))
                elems.append(_RL_Sp(1, 0.35 * _RL_CM))

        doc.build(elems)

        n_recos = len(_sess._accumulated_recommendations)
        n_ch = sum(
            1
            for r in _sess._accumulated_recommendations
            if isinstance(r, dict)
            and r.get("chart_path")
            and os.path.exists(r["chart_path"])
        )
        return (
            f"\u2705 PDF saved \u2192 {PDF_PATH}\n"
            f"   Queries: {len(_sess._query_history)} | "
            f"Charts embedded: {n_ch} | Recommendations: {n_recos}"
        )
    except Exception as e:
        import traceback

        return f"\u274c PDF generation failed: {e}\n{traceback.format_exc()[-500:]}"
