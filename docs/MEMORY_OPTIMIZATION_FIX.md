# Memory Optimization Fix — Remove the Embeddings / FAISS / Torch Stack

> **Audience:** an LLM (or engineer) tasked with making the NAMAA Analytics Agent fit a
> low-memory host (e.g. Railway/Render 512 MB free tier). This document is a complete,
> self-contained spec: read it and reproduce the change exactly.

---

## 1. Problem statement

The app's resident memory is **~700 MB–1 GB**, which OOM-kills the process on a 512 MB
container. The single largest contributor (**~450–500 MB**) is the local embeddings stack:

- `torch` (CPU) — ~200–350 MB
- `sentence-transformers` + `transformers` + `huggingface-hub` — ~200 MB
- the MiniLM model weights (`paraphrase-multilingual-MiniLM-L12-v2`) — ~120–150 MB
- `faiss-cpu` + the loaded index — ~30–80 MB

That stack exists to power **only two features**:

1. **Semantic cache** — embeds a question and cosine-matches it against prior questions.
2. **FAISS schema retrieval** — `_build_schema_context()` semantic-searches a vector index
   of the DWH schema to feed the SQL prompt.

**Key insight:** the DWH schema is tiny and fixed (~6 tables). Retrieval is overkill — the
whole schema fits in the prompt as a static string. And the exact-match cache still works
without embeddings. So we can delete the entire embeddings/FAISS/torch stack.

**Expected result:** RSS drops from ~700 MB–1 GB to **~200–300 MB** (fits 512 MB with margin).

**Trade-off (accepted):** the **semantic** cache is disabled (paraphrased questions no longer
hit cache). The **exact-match** cache still works. SQL quality is unaffected because the full
schema is always in the prompt.

---

## 2. Scope of change (files touched)

| File | Change |
|---|---|
| `src/config.py` | Remove `HuggingFaceEmbeddings`, `FAISS` imports + the `_LazyEmbeddings`/`_LazyVectorStore` classes and the `embeddings`/`vector_store` singletons. |
| `src/executor.py` | Replace `_build_schema_context()` (FAISS search) with a function returning a **static schema string**. Drop the `vector_store` import. |
| `src/session.py` | Make `_semantic_lookup()` a no-op returning `None`; make `_semantic_store()` a no-op. Drop the `embeddings` imports inside them. |
| `src/pipeline_stages/sql_phase.py` | No logic change needed — it calls `_build_schema_context()`, which now returns the static string. (Optional: it can call the static function directly.) |
| `requirements.txt` | Remove `torch`, `sentence-transformers`, `transformers`, `huggingface-hub`, `faiss-cpu`, `langchain-huggingface`, and the `--extra-index-url` torch line. |
| `data/faiss_dwh_index/` | No longer loaded at runtime; may be left in the repo or deleted. |

Do **not** touch: the SQL generation loop, the cache dict itself (`_query_cache`), the exact
cache path (`try_exact` in `cache_serve.py`), charts, or the pipeline structure.

---

## 3. Step-by-step edits

### 3.1 `src/config.py` — delete the embeddings/FAISS machinery

**Remove these imports** (near the top):

```python
from langchain_community.vectorstores import FAISS
from langchain_huggingface import HuggingFaceEmbeddings
```

**Remove the entire lazy-loading block** — the `_LazyEmbeddings` class, the `_LazyVectorStore`
class, and the two module-level singletons:

```python
# DELETE everything from:
class _LazyEmbeddings:
    ...
# through:
embeddings = _LazyEmbeddings()
vector_store = _LazyVectorStore()
```

**Keep** `INDEX_DIR` path constant only if something else references it; otherwise it can go.
Everything else in `config.py` (engine, constants, date range, Plotly template) stays.

> There is no need to keep `embeddings = None` / `vector_store = None` stubs **unless** an
> import elsewhere still references them. After the edits in 3.2 and 3.3 nothing imports them,
> so remove them entirely. If you prefer a defensive stub, set `embeddings = None` and
> `vector_store = None` — the no-op guards in 3.3 already handle `None`.

### 3.2 `src/executor.py` — static schema instead of FAISS retrieval

**Change the import** (remove `vector_store`):

```python
# BEFORE
from src.config import (
    orders, products, order_items, categories, subcategories, vector_store,
    ...
)
# AFTER — drop vector_store from the list
from src.config import (
    orders, products, order_items, categories, subcategories,
    ...
)
```

**Replace `_build_schema_context()`** entirely. The current version does a FAISS
`similarity_search_with_score`. Replace with a constant string covering the whole `dwh1`
star schema. The `question`/`top_k` args are kept for signature compatibility but ignored:

```python
# Static DWH schema — the schema is small and fixed, so we inline it instead of
# retrieving it from a FAISS vector index (removes the torch/transformers/faiss stack,
# ~450-500 MB RAM). Keep this in sync with the actual dwh1 schema.
_STATIC_SCHEMA_CONTEXT = """
Table: dwh1.fact_order_item  (main fact, ~233k rows; measures pre-computed)
  keys: order_item_key (PK), order_id, order_date_key (FK dim_date),
        product_key (FK dim_product), category_key (FK dim_category),
        brand_key (FK dim_brand), customer_key, seller_key, delivery_date_key, data_owner_key
  measures: total_amount (USE THIS for revenue), quantity, unit_price,
            discount_amount (ALWAYS 0 in this data), tax_amount
  attrs: order_status
---
Table: dwh1.dim_date
  date_key (PK, yyyymmdd int), full_date, year, month, month_name, week, day, quarter
---
Table: dwh1.dim_product
  product_key (PK), name (Arabic), en_name (Latin), category_key, subcategory_key,
  price, tax_rate
---
Table: dwh1.dim_category
  category_key (PK), category_name (Arabic), sub_category_name (Arabic)  -- BOTH levels in one row
---
Table: dwh1.dim_brand
  brand_key (PK), brand_name (Arabic, complete), brand_en_name (Latin, ~50% empty)
---
Table: dwh1.dim_customer / dwh1.dim_seller
  name/city columns are ALL NULL — never filter by them
""".strip()


def _build_schema_context(question: str = "", top_k: int = 5) -> str:
    """Return the full DWH schema as a static string (no vector retrieval).

    The schema is tiny and fixed, so inlining it removes the embeddings/FAISS stack
    entirely. Args are ignored; kept for call-site compatibility."""
    return _STATIC_SCHEMA_CONTEXT
```

> Verify the field names against the real DB (or the existing SQL prompt cheat-sheet in
> `src/prompts.py`) before finalizing — they must match exactly or SQL generation degrades.

### 3.3 `src/session.py` — disable semantic cache (keep exact cache)

**In `_semantic_lookup()`** — make it a no-op that returns `None` (a cache miss). Remove the
`from src.config import embeddings` line and all embedding/numpy work:

```python
def _semantic_lookup(question, use_viz, use_reco, lang="en", top_n=None,
                     time_filter=None, dimension="general", metric="other",
                     intent_type="other"):
    """Semantic cache disabled (no local embeddings model in the low-memory build).
    Returns None so the pipeline treats every non-exact query as a cache miss."""
    return None
```

**In `_semantic_store()`** — make it a no-op:

```python
def _semantic_store(question, cache_key, use_viz, use_reco, lang="en", top_n=None,
                    time_filter=None, dimension="general", metric="other",
                    intent_type="other"):
    """Semantic cache disabled — no-op in the low-memory build."""
    return None
```

Keep both **function signatures identical** to the originals so callers
(`pipeline.py`, `cache_serve.py`) need no changes. Do **not** remove the exact-match cache
(`try_exact` in `cache_serve.py`, `_query_cache` dict) — that stays and still works.

### 3.4 `requirements.txt` — drop the heavy deps

Remove these lines:

```
--extra-index-url https://download.pytorch.org/whl/cpu
torch==...
sentence-transformers==...
transformers==...
huggingface-hub==...
faiss-cpu==...
langchain-huggingface==...
```

Keep `langchain`, `langchain-core`, `langchain-community`, `langchain-groq` (the Groq LLM
path does not depend on torch). Keep everything else.

### 3.5 `data/faiss_dwh_index/` — optional cleanup

Nothing loads it at runtime anymore. Leave it (harmless) or delete it to shrink the repo.

---

## 4. Verification checklist

After the edits, confirm:

1. **No lingering imports of the removed names:**
   ```bash
   grep -rn "HuggingFaceEmbeddings\|from langchain_community.vectorstores\|vector_store\|langchain_huggingface" src/
   ```
   Should return nothing (or only comments). `embeddings` should only appear if you kept a
   `None` stub.

2. **Syntax parses:**
   ```bash
   python -c "import ast,glob; [ast.parse(open(f,encoding='utf-8').read()) for f in glob.glob('src/**/*.py',recursive=True)+['app.py']]"
   ```

3. **App imports without pulling torch:**
   ```bash
   python -c "import app" 2>&1 | grep -i "torch\|faiss\|sentence" && echo "STILL LOADING HEAVY DEPS" || echo "clean"
   ```

4. **Functional:** a first-time (non-cached) question still returns SQL + chart. An
   **exact-repeat** of that question hits the exact cache. A **paraphrase** now MISSES
   (expected — semantic cache is off).

5. **Memory:** measure RSS after a query (`ps -o rss= -p <pid>` / container metrics). Expect
   **~200–300 MB**, down from ~700 MB–1 GB.

---

## 5. Rollback / re-enabling

To restore the semantic cache + FAISS later (on a larger host): revert sections 3.1–3.4
(git revert the commit). The `data/faiss_dwh_index/` must exist if you re-enable FAISS.

---

## 6. Notes & caveats

- **Keep the static schema in sync with the DB.** If the DWH schema changes, update
  `_STATIC_SCHEMA_CONTEXT` in `executor.py` — there is no longer an index to regenerate.
- **`langchain-groq` does not need torch** — the LLM calls are HTTP; removing torch does not
  affect SQL/NL generation.
- **Exact cache still saves tokens** for repeated identical questions; only paraphrase-matching
  is lost.
- **Do not run multiple web workers** (`gunicorn --workers N`) on free tier — each worker
  re-imports the process; even without torch this multiplies base RAM. Use 1 worker
  (Gradio's `demo.launch()` is single-process by default).
