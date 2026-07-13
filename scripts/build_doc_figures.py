# -*- coding: utf-8 -*-
"""
build_doc_figures.py
====================
Generates the three architecture figures (A, B, C) for the NAMAA documentation as
brand-themed PNGs using matplotlib. Deterministic, accurate labels, no AI drift.

Out (docs/figures/):
  figure_a_architecture.png
  figure_b_pipeline.png
  figure_c_text_to_sql.png
"""
import os
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch
from matplotlib.lines import Line2D

# ── Brand palette ─────────────────────────────────────────────
INDIGO   = "#3D1B6A"
INDIGO_L = "#8A45B2"
CYAN     = "#06b6d4"
BLUE     = "#1e40af"
AMBER    = "#f59e0b"
GREEN    = "#059669"
INK      = "#030213"
GREY     = "#717182"
LINE     = "#c9c6d6"
BG       = "#ffffff"
PANEL    = "#f4f2fa"

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "docs", "figures")
os.makedirs(OUT_DIR, exist_ok=True)

plt.rcParams["font.family"] = "DejaVu Sans"


# ── Helpers ───────────────────────────────────────────────────
def box(ax, x, y, w, h, text, fc="#ffffff", ec=INDIGO, tc=INK, fs=10, bold=False,
        rounded=0.02, lw=1.6, align="center"):
    p = FancyBboxPatch((x, y), w, h,
                       boxstyle=f"round,pad=0.006,rounding_size={rounded}",
                       linewidth=lw, edgecolor=ec, facecolor=fc, zorder=3)
    ax.add_patch(p)
    ax.text(x + w / 2, y + h / 2, text, ha="center", va="center",
            fontsize=fs, color=tc, weight="bold" if bold else "normal",
            zorder=4, wrap=True)
    return (x, y, w, h)


def title_box(ax, x, y, w, h, title, fc=INDIGO, fs=11):
    p = FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.004,rounding_size=0.02",
                       linewidth=0, facecolor=fc, zorder=3)
    ax.add_patch(p)
    ax.text(x + w / 2, y + h / 2, title, ha="center", va="center",
            fontsize=fs, color="white", weight="bold", zorder=4)


def arrow(ax, x1, y1, x2, y2, label=None, color=INDIGO, ls="-", fs=8, rad=0.0,
          lx=None, ly=None):
    a = FancyArrowPatch((x1, y1), (x2, y2),
                        arrowstyle="-|>", mutation_scale=14, linewidth=1.6,
                        color=color, linestyle=ls,
                        connectionstyle=f"arc3,rad={rad}", zorder=2)
    ax.add_patch(a)
    if label:
        ax.text(lx if lx is not None else (x1 + x2) / 2,
                ly if ly is not None else (y1 + y2) / 2 + 0.012,
                label, ha="center", va="center", fontsize=fs, color=GREY,
                style="italic", zorder=5,
                bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none"))


def group_panel(ax, x, y, w, h, label=None):
    p = FancyBboxPatch((x, y), w, h, boxstyle="round,pad=0.004,rounding_size=0.02",
                       linewidth=1.2, edgecolor=LINE, facecolor=PANEL, zorder=1)
    ax.add_patch(p)
    if label:
        # Title sits ABOVE the panel so it never overlaps the first inner box.
        ax.text(x + w / 2, y + h + 0.018, label, ha="center", va="bottom",
                fontsize=9.5, color=INDIGO, weight="bold", zorder=2)


def fig_base(w=15, h=8.2):
    fig, ax = plt.subplots(figsize=(w, h))
    ax.set_xlim(0, 1); ax.set_ylim(0, 1); ax.axis("off")
    fig.patch.set_facecolor(BG)
    return fig, ax


def save(fig, name):
    path = os.path.join(OUT_DIR, name)
    fig.savefig(path, dpi=150, bbox_inches="tight", facecolor=BG)
    plt.close(fig)
    print("saved", path)
    return path


# ══════════════════════════════════════════════════════════════
# FIGURE A — System Architecture Overview
# ══════════════════════════════════════════════════════════════
def figure_a():
    fig, ax = fig_base(15.5, 8.4)
    ax.text(0.5, 0.965, "Figure A — System Architecture Overview",
            ha="center", fontsize=15, color=INDIGO, weight="bold")

    # Column 1 — Client layer
    group_panel(ax, 0.02, 0.28, 0.17, 0.42, "Client Layer")
    box(ax, 0.035, 0.53, 0.14, 0.11, "Web UI\n(HTML + Plotly)", fc="#ffffff", fs=9.5, bold=True)
    box(ax, 0.035, 0.36, 0.14, 0.11, "FastAPI REST\n/ Gradio app", fc="#ffffff", fs=9.5, bold=True)

    # Column 2 — Orchestrator with stage chips
    group_panel(ax, 0.245, 0.12, 0.22, 0.70, "Pipeline Orchestrator  (async · 8 stages)")
    stages = ["0 · Chart-edit intercept", "1 · Spelling · refs · chitchat",
              "2 · Exact cache", "3 · Intent + decompose",
              "4 · Semantic cache", "5 · SQL phase",
              "6 · Parallel enrichment", "7 · Finalize"]
    sy = 0.725
    for i, s in enumerate(stages):
        box(ax, 0.258, sy - i * 0.073, 0.194, 0.056, s, fc="#efe9f6",
            ec=INDIGO_L, fs=8.3, rounded=0.02, lw=1.0)

    # Column 3 — services
    group_panel(ax, 0.515, 0.14, 0.235, 0.68, "Intelligence & Data Services")
    box(ax, 0.53, 0.65, 0.205, 0.13,
        "Groq LLMs — RotatingKeyLLM\nLLaMA-3.3-70B + 4 fallbacks",
        fc="#eef4ff", ec=BLUE, fs=8.6, bold=True)
    box(ax, 0.53, 0.47, 0.205, 0.13,
        "FAISS schema index\n(MiniLM embeddings, lazy-loaded)",
        fc="#e7fbff", ec=CYAN, fs=8.6, bold=True)
    box(ax, 0.53, 0.18, 0.205, 0.20,
        "PostgreSQL DWH\nSupabase · schema dwh1\n(star schema: 1 fact + 5 dims)",
        fc="#eefaf3", ec=GREEN, fs=8.6, bold=True)

    # Column 4 — response
    group_panel(ax, 0.79, 0.26, 0.185, 0.42, "Response")
    resp = ["NL answer (AR / EN)", "Chart (Plotly JSON)",
            "Result table", "Recommendations +\nfollow-ups"]
    for i, r in enumerate(resp):
        box(ax, 0.803, 0.575 - i * 0.083, 0.16, 0.064, r, fc="#ffffff",
            ec=INDIGO, fs=8.4, lw=1.0)

    # Arrows
    arrow(ax, 0.175, 0.545, 0.245, 0.52, color=INDIGO)
    ax.text(0.208, 0.565, "question (AR / EN)", ha="center", fontsize=8, color=GREY, style="italic")
    arrow(ax, 0.175, 0.395, 0.245, 0.44, color=INDIGO)
    # orchestrator <-> services (bidirectional feel)
    for yy in (0.71, 0.53, 0.28):
        arrow(ax, 0.465, 0.48, 0.515, yy, color=GREY, rad=0.05)
    # orchestrator -> response
    arrow(ax, 0.465, 0.44, 0.79, 0.46, color=INDIGO)
    ax.text(0.628, 0.468, "results", ha="center", fontsize=8, color=GREY, style="italic",
            bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none"))
    # response -> UI (return, streamed) — routed high above all panels, no clipping
    a = FancyArrowPatch((0.882, 0.70), (0.105, 0.60),
                        arrowstyle="-|>", mutation_scale=14, linewidth=1.6,
                        color=CYAN, linestyle="--",
                        connectionstyle="arc3,rad=-0.30", zorder=6)
    ax.add_patch(a)
    ax.text(0.5, 0.885, "streamed back via SSE", ha="center", fontsize=8.5,
            color=CYAN, style="italic")

    ax.text(0.5, 0.03,
            "End-to-end flow: client → 8-stage async orchestrator → Groq LLMs + FAISS + live PostgreSQL DWH → "
            "answer · chart · table · recommendations, streamed back to the client.",
            ha="center", fontsize=8.5, color=GREY, style="italic")
    return save(fig, "figure_a_architecture.png")


# ══════════════════════════════════════════════════════════════
# FIGURE B — Agent Pipeline (8 stages)
# ══════════════════════════════════════════════════════════════
def figure_b():
    fig, ax = fig_base(15.5, 8.6)
    ax.text(0.5, 0.965, "Figure B — Agent Pipeline (8 stages)",
            ha="center", fontsize=15, color=INDIGO, weight="bold")

    # Left flow of stages (vertical), with early-exit branches on the right
    stages = [
        ("0 · Chart-edit intercept", "edit current chart, no SQL", INDIGO_L),
        ("1 · Spelling · references · chitchat gate", "clean input; regex pre-checks skip LLM", INDIGO),
        ("2 · Exact cache lookup", "SHA-256 key · 0 tokens on hit", CYAN),
        ("3 · Rewrite + Intent & Decompose", "one LLM call → intent + plan JSON", INDIGO),
        ("4 · Semantic cache lookup", "MiniLM cosine ≥ 0.70 · cross-language", CYAN),
        ("5 · SQL phase", "FAISS schema → SQL → validate → execute", BLUE),
        ("6 · Parallel enrichment", "summary · chart · recos · follow-ups", GREEN),
        ("7 · Finalize", "cache store · metrics · final answer", INDIGO),
    ]
    x0, w = 0.06, 0.46
    top = 0.86
    gap = 0.099
    boxes = []
    for i, (t, sub, col) in enumerate(stages):
        y = top - i * gap
        box(ax, x0, y - 0.062, w, 0.07,
            t + "\n" + sub, fc="#ffffff", ec=col, fs=9, lw=1.6)
        boxes.append((x0, y, w))
        if i > 0:
            arrow(ax, x0 + w / 2, top - (i - 1) * gap - 0.062, x0 + w / 2, y, color=GREY)

    # START / END markers
    ax.text(x0 + w / 2, 0.895, "▼ user question", ha="center", fontsize=9, color=INK, weight="bold")

    # Early-exit branches (right side)
    ex = 0.62
    def exit_branch(from_i, label, col):
        y = top - from_i * gap - 0.028
        box(ax, ex, y - 0.03, 0.33, 0.06, label, fc="#fbeef2", ec="#d4183d", fs=8.5, lw=1.1)
        arrow(ax, x0 + w, y, ex, y, color="#d4183d", ls="--")
    exit_branch(0, "chart updated → return", "#d4183d")
    exit_branch(1, "chitchat reply → return", "#d4183d")
    exit_branch(2, "exact cache hit → return (0 tokens)", "#d4183d")
    exit_branch(4, "semantic cache hit → return", "#d4183d")

    # Enrichment fan-out detail (right, lower)
    group_panel(ax, 0.60, 0.06, 0.37, 0.24, "Stage 6 — parallel (asyncio)")
    for i, (lab, col) in enumerate([("NL summary (streamed)", GREEN),
                                    ("Chart (deterministic spec)", INDIGO),
                                    ("Recommendations", AMBER),
                                    ("Follow-up questions", CYAN)]):
        bx = 0.615 + (i % 2) * 0.18
        by = 0.17 - (i // 2) * 0.075
        box(ax, bx, by, 0.165, 0.055, lab, fc="#ffffff", ec=col, fs=8, lw=1.0)
    arrow(ax, x0 + w, top - 6 * gap - 0.028, 0.60, 0.20, color=GREEN, rad=-0.1)

    ax.text(0.5, 0.02,
            "Each stage short-circuits when possible (chart-edit, cache hit, or chitchat return immediately); "
            "only a genuine cache-missing analytics query runs the full path.",
            ha="center", fontsize=8.5, color=GREY, style="italic")
    return save(fig, "figure_b_pipeline.png")


# ══════════════════════════════════════════════════════════════
# FIGURE C — Text-to-SQL Data Flow
# ══════════════════════════════════════════════════════════════
def figure_c():
    fig, ax = fig_base(15.5, 7.0)
    ax.text(0.5, 0.95, "Figure C — Text-to-SQL Data Flow (single query)",
            ha="center", fontsize=15, color=INDIGO, weight="bold")

    steps = [
        ("Rewritten\n+ classified\nquestion", "#ffffff", INK),
        ("FAISS schema\nretrieval\n(top-K chunks)", "#e7fbff", CYAN),
        ("LLM SQL\ngeneration\n(SQL_PROMPT)", "#eef4ff", BLUE),
        ("Multi-layer\nSQL safety\nvalidation", "#fff6e5", AMBER),
        ("Execute on\ndwh1\n(read-only)", "#eefaf3", GREEN),
        ("Sanity\ncheck", "#f4f2fa", INDIGO),
    ]
    n = len(steps)
    w, h = 0.135, 0.20
    gap = (1 - 0.06 * 2 - n * w) / (n - 1)
    x = 0.06
    y = 0.5
    centers = []
    for i, (t, fc, ec) in enumerate(steps):
        box(ax, x, y, w, h, t, fc=fc, ec=ec, fs=9, lw=1.6, bold=False)
        centers.append((x, x + w))
        if i > 0:
            arrow(ax, centers[i - 1][1], y + h / 2, x, y + h / 2, color=GREY)
        x += w + gap

    # Success path down to chart
    last_x0, last_x1 = centers[-1]
    box(ax, last_x1 - w, 0.14, w, 0.13, "Deterministic\nchart spec →\nrendered chart",
        fc="#efe9f6", ec=INDIGO_L, fs=9, lw=1.6)
    arrow(ax, (last_x0 + last_x1) / 2, y, (last_x0 + last_x1) / 2, 0.27,
          "pass", color=GREEN, lx=(last_x0 + last_x1) / 2 + 0.03, ly=0.40)

    # Retry loop: sanity check -> back to SQL gen
    sql_c = centers[2]
    arrow(ax, (last_x0 + last_x1) / 2, y + h, (sql_c[0] + sql_c[1]) / 2, y + h,
          "fail → feed error back, retry (≤3)", color="#d4183d", ls="--",
          rad=-0.35, lx=0.5, ly=0.80)

    # Safety detail chips under validation box
    val_c = centers[3]
    ax.text((val_c[0] + val_c[1]) / 2, 0.44,
            "DDL blocked · single SELECT · LIMIT 10000 · READ ONLY txn",
            ha="center", fontsize=7.4, color=GREY, style="italic")

    ax.text(0.5, 0.03,
            "Compound questions split into ≤2 parallel sub-steps merged by one of six deterministic pandas "
            "combiners (merge_on_key · pct_change · subtract · ratio · filter_by_step1 · display_separately).",
            ha="center", fontsize=8.5, color=GREY, style="italic")
    return save(fig, "figure_c_text_to_sql.png")


if __name__ == "__main__":
    figure_a()
    figure_b()
    figure_c()
    print("ALL FIGURES DONE →", OUT_DIR)
