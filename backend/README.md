# Backend

| Layer | File | What it does |
|---|---|---|
| Decision | `app/layers/decision.py` | RAG engine: Art. 54 rejects, Art. 53 accepts, see below |
| Routing | `app/layers/routing.py` | statistics, SPE or downgrade (RAG check of Art. 66); adds Art. 66/68/69/73 |
| Mapping | `app/layers/mapping.py`, `llm_mapping.py` | definitions → `QuerySpec` (rules, then IBM Bob) |
| Query | `app/layers/query.py` | `QuerySpec` → SQLite SQL → answer |
| Small-cell rule | `app/layers/small_cell.py` | `<10`, `suppressed`, one decimal half up |
| SPE copy | `app/layers/spe.py` | stub |

Interfaces: `app/models.py`. Wiring: `app/pipeline.py`. API: `app/main.py`
(`POST /api/requests`; `/api/submissions` still returns placeholder results).

## Setup and run

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/uvicorn app.main:app --reload --port 8000
.venv/bin/pytest -m "not real_llm"                          # fast, no Ollama
.venv/bin/python -m app.eval_decisions                      # accept/reject: 10 samples + 16 held-out
.venv/bin/python scripts/evaluate.py --set tasks --runs 3   # full decision + articles on R01-R10
.venv/bin/python scripts/evaluate.py --split test --failures  # full decision, 36 held-out requests
.venv/bin/python tests/fixtures/blackbox/run.py             # data extraction: 100 black-box requests
```

Rebuild the committed databases only after changing their sources:
`python -m app.db` (`data/ehds.db` from the CSVs), `python -m app.rag.corpus` (`data/law.db` from `eulex.html`).

| Variable | Default | Use |
|---|---|---|
| `OPENROUTER_API_KEY` | — | required; in `backend/.env` or the environment |
| `EHDS_LLM_MODEL` | `anthropic/claude-haiku-5.5` | OpenRouter model for decision and routing |
| `EHDS_OPENROUTER_TIMEOUT` | `60` | seconds per OpenRouter call |
| `EHDS_EMBED_MODEL` | `nomic-embed-text` | embeddings; rebuild `law.db` after changing it |
| `EHDS_OLLAMA_URL` | `http://localhost:11434` | Ollama for the embeddings |
| `EHDS_LLM` | `on` | `off` disables IBM Bob in data extraction |

## Decision and routing (RAG)

- `eulex.html` is split into 406 chunks (each point of Art. 50–81, the recitals) and embedded in `data/law.db`.
- Decision: the LLM extracts the **end use** from organisation and purpose; the 3 closest points of
  Art. 53(1) and of Art. 54 are retrieved; the LLM picks one and quotes it. Words not in the law →
  `needs_review`. Art. 54 point → reject, Art. 53 point → accept.
- Routing: a health data request gets statistics (Art. 69). For an access application, Art. 66(2)
  and 66(3) are fetched from the store and the LLM decides whether the application shows that its
  purpose cannot be met with anonymised statistics, quoting the sentence. Quote found verbatim in
  the request → SPE (66, 68, 73); otherwise downgrade (66, 69), flagged `spe_quote_unverified`
  when a justification was claimed but could not be quoted.
- Claude Haiku 5.5 via OpenRouter (strict JSON schema, routed only to endpoints that enforce it); only request text leaves the
  app, never patient data. Answers are cached on disk in `.cache/openrouter/`, so the same request
  gets the same decision after a restart. Embeddings: local `nomic-embed-text` via Ollama.
  Decisions and routes are cached per request content; passing a retriever/LLM explicitly
  (tests, eval) bypasses the cache.
- `app/purpose/` (IBM Bob / NLI / rules purpose check) is no longer used by the pipeline.
- Evaluation set: `tests/fixtures/eval/requests.json` (100 requests, tune on `dev`, report `test`).

## Data extraction

![From English to SQL](../docs/english-to-sql.svg)

- Each definition must fully match a regex rule; the report line is read via the defined names.
  Anything not understood raises `InterpretError`, never skipped.
- Where the rules give up, IBM Bob (`bob run --mode ask`, request text only) fills the same
  `QuerySpec`; same checks, codes must come from the request, answers cached in `.cache/llm/`,
  result flagged `llm_interpreted`. Bob is not installed in our setup (to do: local Ollama instead).
- One patient-ID CTE per concept, no correlated subqueries; rounding and small cells in Python.
- Black-box set in `tests/fixtures/blackbox/`, written by a separate agent; `run.py --verbose ID` shows one case.
