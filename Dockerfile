# ──────────────────────────────────────────────────────────────────────────────
# NAMAA Analytics Agent — FastAPI service (api:app)
# Two-stage build: compile wheels in a builder, ship only the runtime.
# ──────────────────────────────────────────────────────────────────────────────

# ── Stage 1: builder — install Python deps (needs a compiler for some wheels) ──
FROM python:3.11-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# build-essential is only needed to compile packages from source at install time;
# it stays in this stage and never reaches the final image.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Build wheels into a self-contained venv so the runtime stage can copy it whole.
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# requirements.txt pins CPU-only torch via --extra-index-url so pip never pulls
# the CUDA build (~1.2 GB saved).
COPY requirements.txt .
RUN pip install -r requirements.txt


# ── Stage 2: runtime — slim image with just the venv + app + assets ───────────
FROM python:3.11-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PATH="/opt/venv/bin:$PATH" \
    PORT=8000 \
    # kaleido 1.x drives a real Chromium (via choreographer) to render Plotly chart
    # PNGs for the PDF report. choreographer reads BROWSER_PATH to find the binary.
    BROWSER_PATH=/usr/bin/chromium

WORKDIR /app

# System runtime deps:
#   fonts-noto-core  → system-wide Arabic glyphs for PDF export (the app also bundles
#                      its own font in assets/fonts/; this is a belt-and-suspenders fallback).
#   curl             → used by the container HEALTHCHECK below.
#   chromium         → headless browser kaleido needs to render chart PNGs into the PDF.
#   (the remaining libs are Chromium's shared-library dependencies for headless mode.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    fonts-noto-core \
    curl \
    chromium \
    libnss3 libnspr4 libdbus-1-3 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
    libdrm2 libxkbcommon0 libatspi2.0-0 libxcomposite1 libxdamage1 \
    libxfixes3 libxrandr2 libgbm1 libasound2 \
    && rm -rf /var/lib/apt/lists/*

# Bring over the pre-built virtualenv from the builder (no compiler in this image).
COPY --from=builder /opt/venv /opt/venv

# App code + runtime assets. .dockerignore keeps out .env, .git, caches, scripts/, etc.
# The FAISS index (data/faiss_dwh_index), Arabic fonts (assets/fonts) and the static
# UI (static/) are committed to the repo, so they are copied in here.
COPY . .

# Run as a non-root user.
RUN useradd --create-home --uid 10001 appuser \
    && chown -R appuser:appuser /app
USER appuser

# Documented default; the app binds to $PORT (Railway/Cloud Run set this at runtime).
EXPOSE 8000

# Liveness probe hits the app's /health endpoint.
HEALTHCHECK --interval=30s --timeout=5s --start-period=40s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/health" || exit 1

# Shell form so ${PORT} expands. Single worker: the app keeps per-process state
# (session store, embeddings, FAISS index) in memory — multiple workers would each
# hold a separate copy and split sessions across processes.
CMD uvicorn api:app --host 0.0.0.0 --port ${PORT} --workers 1
