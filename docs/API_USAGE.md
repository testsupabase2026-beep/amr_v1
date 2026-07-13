# NAMAA Analytics — REST API Usage

A framework-free HTTP interface to the analytics agent (alternative to the Gradio app).
Same pipeline, same functionality: NL→SQL, charts, recommendations, follow-ups, cache,
compound queries, chart-edit, sessions, exports.

---

## 1. Run the server

```bash
cd "F:/deeb analytics refactor/deeb-analytics"
uvicorn api:app --host 0.0.0.0 --port 8000
```

- Web UI:            <http://127.0.0.1:8000/>
- Interactive docs:  <http://127.0.0.1:8000/docs>  (Swagger — "Try it out" on every endpoint)
- ReDoc:             <http://127.0.0.1:8000/redoc>

> Run with a **single worker** (default). The session store is in-process; multiple workers
> would not share it. `$PORT` env var is honoured if set (for PaaS).

---

## 2. Endpoints at a glance

| Method | Path | Purpose |
|---|---|---|
| POST | `/ask` | Run one query, return the final result (waits for completion) |
| POST | `/ask/stream` | Same, but **streams** chunks via SSE (summary appears token-by-token) |
| POST | `/clear` | Reset a session's memory + cache |
| POST | `/session/save` | Save the session to `sessions/{id}.json` |
| POST | `/session/load` | Restore the session from disk |
| GET  | `/export/csv` | Download the last result as CSV |
| GET  | `/export/pdf` | Download the full PDF report |
| GET  | `/kpis` | KPI snapshot (HTML fragment, no LLM) |
| GET  | `/health` | Liveness + DB connectivity |
| GET  | `/` | The web UI |

---

## 3. `POST /ask` — the main endpoint

**Request body** (JSON):

| Field | Type | Default | Meaning |
|---|---|---|---|
| `question` | string | — (required) | The natural-language question (Arabic or English) |
| `use_viz` | bool | `true` | Generate a chart |
| `use_reco` | bool | `true` | Generate business recommendations |
| `use_cache` | bool | `true` | Use/populate the exact + semantic cache |
| `session_id` | string | `null` | Groups requests into one conversation (see §6) |

**Response** (JSON) — the final result:

| Field | Type | Description |
|---|---|---|
| `chat_text` | string | Natural-language answer (same as `summary`) |
| `summary` | string | 2–3 sentence NL summary of the result |
| `result_html` | string | Styled HTML table of the result — drop into a `<div>` |
| `chart_json` | string | **Plotly JSON spec** (a *string*) — the field to render charts from |
| `chart_html` | string | Self-contained Plotly HTML (only works inside an `<iframe srcdoc>`) |
| `reco_text` | string | Business recommendations (markdown-ish) |
| `followup` | string[] | 3 suggested follow-up questions |
| `log` | string | Step-by-step pipeline log (debug) |
| `tokens_used` | int | LLM tokens spent on this call |
| `done` | bool | Always `true` on `/ask` (final chunk) |
| `session_id` | string | The session this ran under |

> `chart_fig` (the live Plotly Figure) is intentionally **not** returned — it isn't
> JSON-serializable. Use `chart_json`.

**curl:**

```bash
curl -X POST http://127.0.0.1:8000/ask \
  -H "Content-Type: application/json" \
  -d '{"question":"top 5 categories by revenue in 2025","use_reco":true}'
```

**Python:**

```python
import requests
r = requests.post("http://127.0.0.1:8000/ask", json={
    "question": "أكثر 5 فئات إيرادات في 2025",
    "use_viz": True, "use_reco": True, "session_id": "user-123",
})
data = r.json()
print(data["summary"])
print(data["reco_text"])
chart_json = data["chart_json"]     # feed to Plotly on the frontend
```

**JavaScript (fetch):**

```javascript
const res = await fetch("http://127.0.0.1:8000/ask", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ question: "revenue by category in 2024", session_id: "user-123" }),
});
const data = await res.json();
```

---

## 4. Rendering the chart (the important part)

`chart_json` is a **string** containing a Plotly spec `{ "data": [...], "layout": {...} }`.

**Do this** (works in any frontend):

```html
<!-- Plotly.js 2.20+ required (older versions can't decode base64 array data) -->
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<div id="chart"></div>
<script>
  const spec = JSON.parse(data.chart_json);          // it's a STRING → parse it
  Plotly.newPlot("chart", spec.data, spec.layout, { responsive: true });
</script>
```

**Do NOT** do `div.innerHTML = data.chart_html` — browsers don't execute `<script>` tags
inserted via innerHTML, so the chart never draws. `chart_html` only works inside an
`<iframe srcdoc="...">` (where scripts run). For a custom frontend, **always prefer
`chart_json`.**

React:

```jsx
import Plotly from "plotly.js-dist-min";
useEffect(() => {
  const spec = JSON.parse(chartJson);
  Plotly.newPlot(ref.current, spec.data, spec.layout, { responsive: true });
}, [chartJson]);
```

---

## 5. `POST /ask/stream` — streaming (SSE)

Same request body as `/ask`. Returns `text/event-stream`: a sequence of
`data: {json}\n\n` lines, ending with `data: [DONE]`. Early chunks carry the summary as it
streams; the **last** chunk before `[DONE]` has the full payload (chart, table, recos, follow-ups).

**JavaScript:**

```javascript
const res = await fetch("/ask/stream", {
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify({ question: "monthly revenue trend in 2024", session_id: "user-123" }),
});
const reader = res.body.getReader();
const dec = new TextDecoder();
let buf = "";
while (true) {
  const { value, done } = await reader.read();
  if (done) break;
  buf += dec.decode(value, { stream: true });
  let i;
  while ((i = buf.indexOf("\n\n")) >= 0) {
    const line = buf.slice(0, i).trim(); buf = buf.slice(i + 2);
    if (!line.startsWith("data:")) continue;
    const p = line.slice(5).trim();
    if (p === "[DONE]") continue;
    const d = JSON.parse(p);
    // update UI: d.summary streams; the final d has chart_json / result_html / reco_text
  }
}
```

**Python:**

```python
import requests, json
with requests.post("http://127.0.0.1:8000/ask/stream",
                   json={"question": "monthly revenue trend in 2024"}, stream=True) as r:
    for raw in r.iter_lines(decode_unicode=True):
        if not raw or not raw.startswith("data:"):
            continue
        p = raw[5:].strip()
        if p == "[DONE]":
            break
        d = json.loads(p)
        if d.get("done"):
            print("chart_json len:", len(d.get("chart_json") or ""))
```

---

## 6. Sessions (follow-ups, chart-edit, cache)

Pass a stable `session_id` to keep a conversation together. Same id → shared history, cache,
and last result, so these work:

- **Follow-ups:** `"top 10 products"` then `"now only in 2024"` (references resolved from history).
- **Chart-edit:** after a chart, `"flip to bar"` / `"اقلب لأعمدة"` edits it without re-running SQL.
- **Per-user cache** and **save/load**.

Omit `session_id` → everything shares one default session (fine for a single-user demo,
not for concurrent users).

```bash
# clear a session
curl -X POST http://127.0.0.1:8000/clear -H "Content-Type: application/json" \
  -d '{"session_id":"user-123"}'

# save / load
curl -X POST http://127.0.0.1:8000/session/save -H "Content-Type: application/json" -d '{"session_id":"user-123"}'
curl -X POST http://127.0.0.1:8000/session/load -H "Content-Type: application/json" -d '{"session_id":"user-123"}'
```

---

## 7. Exports & KPIs

```bash
# CSV of the last result (pass the same session_id you queried with)
curl -OJ "http://127.0.0.1:8000/export/csv?session_id=user-123"

# PDF report of the session
curl -OJ "http://127.0.0.1:8000/export/pdf?session_id=user-123"

# KPI snapshot (HTML fragment)
curl http://127.0.0.1:8000/kpis
```

CSV export returns **400** if no query has run in that session yet.

---

## 8. Health

```bash
curl http://127.0.0.1:8000/health
# {"status":"ok","db":true,"schema":"dwh1"}
```

`db:false` means the DWH connection is down — check `.env` credentials.

---

## 9. Notes & gotchas

- **Chart may be empty (`chart_json:""`)** for single-value KPIs or chitchat — those have no chart.
- **`chart_json` uses base64-encoded arrays** (Plotly ≥5 default). Frontend Plotly.js must be
  **≥2.20** to decode them; otherwise axes render empty.
- **Arabic text** in `chart_json`/`summary` is already glyph-shaped — renders correctly, no
  extra handling needed.
- **Latency:** a non-cached query is ~20–40s (LLM-bound: SQL gen + enrichment). Prefer
  `/ask/stream` so the user sees the summary immediately. Cache hits are sub-second.
- **CORS** is open (`*`) for development — restrict `allow_origins` in `api.py` before production.
- **Auth:** none by default. Add an API-key dependency before exposing publicly.
