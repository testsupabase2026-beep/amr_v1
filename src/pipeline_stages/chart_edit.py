"""
chart_edit.py
=============
Chart-edit intercept — if the user's question is a chart modification request
(flip to bar, recolor, etc.) and a previous chart exists, apply the edit and
short-circuit the pipeline.
"""
from __future__ import annotations

import os
import uuid
from typing import AsyncIterator

import src.session as _sess
from src.config import BASE_DIR, CHART_PNG_HEIGHT, CHART_PNG_WIDTH
from src.executor import _exec_code, _harvest_fig, _sanitize_chart_code
from src.intent import _apply_chart_edit, _is_chart_edit
from utils.formatting import _df_to_html, _format_number_cols, _make_kpi_card, prettify_fig

from src.pipeline_stages.state import PipelineState


async def run(state: PipelineState) -> AsyncIterator[dict]:
    """
    If the current question is a chart-edit, apply it and yield done=True.
    Otherwise yield nothing — the orchestrator continues to the next stage.
    """
    # Editable if we have EITHER stored Plotly source OR a prior figure object.
    # Deterministic/compound charts leave _last_plotly_code empty but always set
    # _last_fig, so gating on the figure lets edits work for every chart type.
    if _sess._last_fig is None and not _sess._last_plotly_code:
        return
    if not await _is_chart_edit(state.question):
        return

    state.log("🎨 Interpreted as a chart-edit request.")
    try:
        # ── Deterministic edit first (reliable Plotly transforms, no LLM) ──
        # Handles flip/orientation, colour, title, sort, pie/area/line, theme,
        # axis-reverse with correct data transforms. Falls through to the LLM
        # only for edits it doesn't recognize.
        if _sess._last_fig is not None:
            from src.chart_edits import apply_deterministic_edit
            det_fig, det_label = apply_deterministic_edit(
                _sess._last_fig, state.question
            )
            if det_fig is not None:
                state.log(f"   ⚙️ Deterministic edit: {det_label}")
                fig = prettify_fig(det_fig, _sess._last_result)
                _sess._last_fig = fig
                # keep source in sync for any subsequent LLM edit
                _sess._last_plotly_code = ""
                chart_html = fig.to_html(full_html=False, include_plotlyjs=True)
                chart_json = fig.to_json()
                try:
                    png_path = os.path.join(BASE_DIR, f"temp_chart_{uuid.uuid4().hex[:8]}.png")
                    fig.write_image(png_path, width=CHART_PNG_WIDTH, height=CHART_PNG_HEIGHT)
                    _sess._session_charts.append(png_path)
                    _sess._update_last_recommendation_chart(png_path)
                except Exception as e:
                    state.log(f"   ⚠️ Could not re-save edited chart PNG: {e}")
                state.early_return = True
                yield state.yield_dict(
                    chat_text="✅ Chart updated.",
                    result_html=(
                        _make_kpi_card(_sess._last_result)
                        if _sess._last_result is not None and _sess._last_result.shape[0] == 1
                        else _df_to_html(_format_number_cols(_sess._last_result))
                    ),
                    chart_html=chart_html,
                    chart_json=chart_json,
                    chart_fig=fig,
                    summary="Chart updated.",
                    done=True,
                )
                return

        # Seed for the LLM edit: prefer the original Plotly source if we have it
        # (LLM path). For deterministic / compound charts there is no stored
        # source, so we describe the current figure as a `go.Figure(...)` seed —
        # but the edit is ALWAYS executed against the real figure object injected
        # as `fig` (see below), so the seed only needs to give the LLM context.
        have_source = bool(_sess._last_plotly_code)
        if have_source:
            seed_code = _sess._last_plotly_code
        else:
            # Compact, human-readable context (NOT executed): current traces +
            # layout title so the LLM edits `fig` correctly.
            f = _sess._last_fig
            tr = f.data[0] if f.data else None
            seed_code = (
                "# The current chart already exists as `fig` (a plotly go.Figure).\n"
                f"# type={getattr(tr,'type',None)}, orientation={getattr(tr,'orientation',None)}\n"
                "# Modify `fig` in place with fig.update_traces(...) / fig.update_layout(...)\n"
                "# or rebuild it from `result`. End with fig.show().\n"
                "fig.show()"
            )

        new_code = await _apply_chart_edit(
            state.question, seed_code, _sess._last_result
        )
        clean_new = _sanitize_chart_code(new_code)

        # Execute the edit. The current figure is ALWAYS pre-injected as `fig`
        # (a deep copy), so surgical fragments like `fig.update_layout(...)` work,
        # AND full rewrites that reassign `fig` also work (they just overwrite it).
        # This is robust whether the chart was LLM-generated or deterministic, and
        # whether the LLM returns a diff or a full rewrite.
        def _run(code: str):
            import copy
            extra = {"result": _sess._last_result}
            if _sess._last_fig is not None:
                extra["fig"] = copy.deepcopy(_sess._last_fig)
            ns = _exec_code(code, extra)
            return _harvest_fig(ns)

        fig = None
        try:
            fig = _run(clean_new)
        except Exception as run_err:
            state.log(f"   ⚠️ Edit code error ({run_err}); retrying with source rebuild.")
            # Last resort: if we DO have original source, run source + edit together.
            if have_source:
                base = _sanitize_chart_code(_sess._last_plotly_code)
                fig = _run(base + "\n" + clean_new)

        if fig is None:
            raise ValueError("No 'fig' produced.")
        fig = prettify_fig(fig, _sess._last_result)
        _sess._last_fig = fig
        _sess._last_plotly_code = new_code
        chart_html = fig.to_html(full_html=False, include_plotlyjs=True)
        chart_json = fig.to_json()

        # Re-render the PNG so the PDF/report uses the EDITED chart, not the
        # originally generated one, and repoint the latest recommendation at it.
        try:
            png_path = os.path.join(BASE_DIR, f"temp_chart_{uuid.uuid4().hex[:8]}.png")
            fig.write_image(png_path, width=CHART_PNG_WIDTH, height=CHART_PNG_HEIGHT)
            _sess._session_charts.append(png_path)
            _sess._update_last_recommendation_chart(png_path)
        except Exception as e:
            state.log(f"   ⚠️ Could not re-save edited chart PNG: {e}")

        state.early_return = True
        yield state.yield_dict(
            chat_text="✅ Chart updated.",
            result_html=(
                _make_kpi_card(_sess._last_result)
                if _sess._last_result.shape[0] == 1
                else _df_to_html(_format_number_cols(_sess._last_result))
            ),
            chart_html=chart_html,
            chart_json=chart_json,
            chart_fig=fig,
            summary="Chart updated.",
            done=True,
        )
        return
    except Exception as e:
        state.log(f"   ⚠️ Chart edit failed: {e}. Falling back to normal pipeline.")
