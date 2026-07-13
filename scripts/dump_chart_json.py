"""
Dump chart_json (and save chart_html) for one query — so you can test rendering in a
custom frontend (see docs/chart_json_test.html).

Usage:
    python scripts/dump_chart_json.py "أكثر 10 منتجات إيرادات"
    python scripts/dump_chart_json.py "revenue by category in 2024"

Writes:
    chart_json.txt   ← paste this into docs/chart_json_test.html
    chart_preview.html ← open directly in a browser (full standalone page)
"""
import asyncio
import sys

from src.session import SessionState, set_session
from src.pipeline import ask_retail_rag_ui


async def _run(question: str):
    set_session(SessionState())
    final = {}
    async for chunk in ask_retail_rag_ui(question=question, use_viz=True,
                                         use_reco=False, use_cache=False):
        final = chunk
    return final


def main():
    if len(sys.argv) < 2:
        print('Usage: python scripts/dump_chart_json.py "<your question>"')
        sys.exit(1)
    question = sys.argv[1]
    print(f"▶ Running: {question}\n")

    out = asyncio.run(_run(question))
    chart_json = out.get("chart_json", "")
    chart_html = out.get("chart_html", "")

    if not chart_json:
        print("⚠️ No chart_json produced (query may not be chartable — e.g. a single KPI).")
        print("   chat_text:", (out.get("chat_text") or "")[:200])
        return

    with open("chart_json.txt", "w", encoding="utf-8") as f:
        f.write(chart_json)
    print(f"✅ chart_json written to chart_json.txt  ({len(chart_json):,} chars)")
    print("   → paste its contents into docs/chart_json_test.html\n")

    # Also drop a fully standalone HTML preview you can just open.
    if chart_html:
        page = (
            "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            "<script src='https://cdn.plot.ly/plotly-2.35.2.min.js'></script></head>"
            "<body><div id='c'></div><script>"
            f"var spec = {chart_json};"
            "Plotly.newPlot('c', spec.data, spec.layout, {responsive:true});"
            "</script></body></html>"
        )
        with open("chart_preview.html", "w", encoding="utf-8") as f:
            f.write(page)
        print("✅ chart_preview.html written — open it in a browser to see it render.")

    # Print a short head of the JSON so you can eyeball it in the terminal too.
    print("\n--- chart_json (first 300 chars) ---")
    print(chart_json[:300])


if __name__ == "__main__":
    main()
