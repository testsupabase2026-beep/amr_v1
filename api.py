"""
api.py
======
FastAPI REST endpoint for the NAMAA Analytics Agent — a framework-free alternative to the
Gradio UI (app.py). It preserves ALL pipeline functionality:

  • NL→SQL analytics with streaming summary
  • charts (chart_html + chart_json)     ← the UI renders chart_json via Plotly.newPlot
  • business recommendations + follow-up questions
  • exact + semantic cache, compound queries, chart-edit, chitchat
  • per-session isolation (optional session_id), save/load session
  • CSV + PDF export, KPI snapshot, health check

Run:
    uvicorn api:app --host 0.0.0.0 --port 8000
    # then open http://127.0.0.1:8000/

Session model:
    Every request may carry a `session_id`. Requests with the SAME id share one SessionState
    (conversation history, cache, last result — so follow-ups / chart-edit / save-load work).
    Requests with no id use a single shared "default" session. Each handler installs the
    session into the ContextVar via set_session(...) exactly like the Gradio handler does.
    (Run with a SINGLE uvicorn worker so the in-process session store is consistent.)
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Optional

from fastapi import FastAPI, Body
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
    StreamingResponse,
)
from pydantic import BaseModel

import src.session as _sess
from src.session import (
    SessionState,
    set_session,
    clear_cache,
    clear_memory,
    save_session,
    load_session,
)
from src.pipeline import ask_retail_rag_ui
from src.kpis import _compute_kpis_html
from src.export import _generate_pdf_report
from src.config import BASE_DIR, EXPORT_CSV, engine, DWH_SCHEMA

app = FastAPI(title="NAMAA Analytics API", version="1.0")

# Allow any origin so a separate frontend (your teammate's) can call it during dev.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

_STATIC_DIR = os.path.join(BASE_DIR, "static")

# ── Session store ─────────────────────────────────────────────
# One SessionState per session_id; a shared "default" when none is given.
_SESSIONS: dict[str, SessionState] = {}


def _get_session(session_id: Optional[str]) -> SessionState:
    key = session_id or "default"
    s = _SESSIONS.get(key)
    if s is None:
        s = SessionState()
        _SESSIONS[key] = s
    return s


def _install(session_id: Optional[str]) -> SessionState:
    """Fetch-or-create the session and install it into the ContextVar for this request."""
    s = _get_session(session_id)
    set_session(s)
    return s


# The live Plotly Figure object is not JSON-serializable — drop it from API payloads.
# Frontends use chart_json (or chart_html); chart_fig is a Gradio-only convenience.
_DROP_KEYS = {"chart_fig"}


def _clean(chunk: dict) -> dict:
    return {k: v for k, v in (chunk or {}).items() if k not in _DROP_KEYS}


# ── Request models ────────────────────────────────────────────
class AskRequest(BaseModel):
    question: str
    use_viz: bool = True
    use_reco: bool = True
    use_cache: bool = True
    session_id: Optional[str] = None


class SessionRequest(BaseModel):
    session_id: Optional[str] = None


# ── Analytics: non-streaming ──────────────────────────────────
@app.post("/ask")
async def ask(req: AskRequest):
    """Run one query end-to-end and return the FINAL result dict.
    (Waits for completion — for token-by-token streaming use /ask/stream.)"""
    if not req.question.strip():
        return JSONResponse({"error": "empty question"}, status_code=400)
    s = _install(req.session_id)
    final: dict = {}
    async for chunk in ask_retail_rag_ui(
        question=req.question,
        use_viz=req.use_viz,
        use_reco=req.use_reco,
        use_cache=req.use_cache,
    ):
        final = chunk  # last chunk carries done=True with the full payload
    out = _clean(final)
    out["session_id"] = s.session_id
    return JSONResponse(out)


# ── Analytics: streaming (Server-Sent Events) ─────────────────
@app.post("/ask/stream")
async def ask_stream(req: AskRequest):
    """Stream every pipeline chunk as SSE (`data: {json}\\n\\n`), ending with `data: [DONE]`.
    The summary arrives token-by-token; the final chunk has the chart/recos/follow-ups."""
    if not req.question.strip():
        return JSONResponse({"error": "empty question"}, status_code=400)
    s = _install(req.session_id)

    async def gen():
        try:
            async for chunk in ask_retail_rag_ui(
                question=req.question,
                use_viz=req.use_viz,
                use_reco=req.use_reco,
                use_cache=req.use_cache,
            ):
                payload = _clean(chunk)
                payload["session_id"] = s.session_id
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
        except Exception as e:  # never leave the stream hanging
            yield f"data: {json.dumps({'error': str(e), 'done': True}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


# ── Session operations ────────────────────────────────────────
@app.post("/clear")
async def clear(req: SessionRequest = Body(default=SessionRequest())):
    _install(req.session_id)
    clear_memory()
    clear_cache()
    return {"status": "cleared"}


@app.post("/session/save")
async def session_save(req: SessionRequest = Body(default=SessionRequest())):
    _install(req.session_id)
    return {"message": save_session()}


@app.post("/session/load")
async def session_load(req: SessionRequest = Body(default=SessionRequest())):
    _install(req.session_id)
    return {"message": load_session()}


# ── Exports ───────────────────────────────────────────────────
@app.get("/export/csv")
async def export_csv(session_id: Optional[str] = None):
    _install(session_id)
    if _sess._last_result is None:
        return JSONResponse({"error": "No result to export yet."}, status_code=400)
    _sess._last_result.to_csv(EXPORT_CSV, index=False, encoding="utf-8-sig")
    return FileResponse(EXPORT_CSV, filename="namaa_result.csv", media_type="text/csv")


@app.get("/export/pdf")
async def export_pdf(session_id: Optional[str] = None):
    _install(session_id)
    # Nothing to report on yet → clear 400 (not a server error).
    if not _sess._accumulated_recommendations and not _sess._query_history:
        return JSONResponse(
            {"error": "No report available — run a query in this session first."},
            status_code=400,
        )
    try:
        # _generate_pdf_report() returns a STATUS STRING (e.g. "✅ PDF saved → <path>\n..."),
        # not a bare path. Parse the .pdf path out of it.
        status = _generate_pdf_report()
    except Exception as e:
        return JSONResponse({"error": f"PDF generation failed: {e}"}, status_code=500)

    # The path may contain spaces (e.g. "F:\deeb analytics refactor\..."), so capture
    # everything up to '.pdf' rather than a whitespace-free token.
    m = re.search(r"→\s*(.+?\.pdf)", status) or re.search(r"([A-Za-z]:\\.+?\.pdf)", status)
    path = m.group(1).strip() if m else None
    if not path or not os.path.exists(path):
        # Generation reported an error, or the path couldn't be resolved.
        return JSONResponse({"error": status.strip()}, status_code=500)
    return FileResponse(path, filename=os.path.basename(path), media_type="application/pdf")


# ── KPI snapshot ──────────────────────────────────────────────
@app.get("/kpis")
async def kpis():
    """Pre-computed KPI snapshot (HTML fragment). No LLM."""
    return HTMLResponse(_compute_kpis_html())


# ── Health ────────────────────────────────────────────────────
@app.get("/health")
async def health():
    db_ok = False
    try:
        if engine is not None:
            with engine.connect() as conn:
                conn.exec_driver_sql("SELECT 1")
            db_ok = True
    except Exception:
        db_ok = False
    return {"status": "ok", "db": db_ok, "schema": DWH_SCHEMA}


# ── UI ────────────────────────────────────────────────────────
@app.get("/", response_class=HTMLResponse)
async def index():
    idx = os.path.join(_STATIC_DIR, "index.html")
    if os.path.exists(idx):
        # no-store so the browser never serves a stale UI after we edit index.html
        return FileResponse(idx, headers={"Cache-Control": "no-store"})
    return HTMLResponse("<h3>UI not found. Expected static/index.html.</h3>", status_code=404)


@app.get("/console", response_class=HTMLResponse)
async def console():
    """Developer console UI — exercises every endpoint with per-endpoint status + a
    'Run all' smoke test."""
    page = os.path.join(_STATIC_DIR, "console.html")
    if os.path.exists(page):
        return FileResponse(page, headers={"Cache-Control": "no-store"})
    return HTMLResponse("<h3>Console not found. Expected static/console.html.</h3>", status_code=404)


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("api:app", host="0.0.0.0", port=port, workers=1)
