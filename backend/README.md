# Backend

| Layer | File | What it does |
|---|---|---|
| Language | `app/layers/language.py` | any EU language → English in one LLM call; English passes through; codes/numbers checked |
| Decision | `app/layers/decision.py` | RAG engine: Art. 54 rejects, Art. 53 accepts, see below |
| Routing | `app/layers/routing.py` | statistics, SPE or downgrade (RAG check of Art. 66); adds Art. 66/68/69/73 |
| Mapping | `app/layers/mapping.py`, `llm_mapping.py` | definitions → `QuerySpec` (rules, then the OpenRouter LLM) |
| Query | `app/layers/query.py` | `QuerySpec` → SQLite SQL → answer |
| Small-cell rule | `app/layers/small_cell.py` | `<10`, `suppressed`, one decimal half up |
| SPE | `app/spe.py`, `app/spe_api.py` | per-permit SQLite copy, access key + applicant name, queries rendered as PNG, `403` download, audit log; UI in `../frontend` |

Interfaces: `app/models.py`. Wiring: `app/pipeline.py`. API: `app/main.py`
(`POST /api/requests`; `/api/spe/...` see [`docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md); `/api/submissions` still returns placeholder results).

## Setup and run

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/uvicorn app.main:app --reload --port 8000 --no-proxy-headers
.venv/bin/pytest -m "not real_llm"                          # fast, no Ollama
.venv/bin/python -m app.eval_decisions                      # accept/reject: 10 samples + 16 held-out
.venv/bin/python scripts/evaluate.py --set tasks --runs 3   # full decision + articles on R01-R10
.venv/bin/python scripts/evaluate.py --split test --failures  # full decision, 36 held-out requests
.venv/bin/python scripts/evaluate.py --lang de                 # only the German held-out requests
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
| `EHDS_LLM` | `on` | `off` disables the LLM fallback in data extraction |
| `EHDS_SPE_DIR` | `backend/spe` | where the per-permit SPE files are written (gitignored) |
| `EHDS_SPE_KEY_TTL_HOURS` | `24` | how long an SPE access key stays valid |
| `EHDS_SPE_FRONTEND_URL` | `http://localhost:5173` | link returned with the access key |
| `EHDS_CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | origins allowed to call the API |
| `EHDS_RATE_LIMIT_REQUESTS` | `20` | `/api/` calls one IP may make per window before it is blocked (429 + `Retry-After`) |
| `EHDS_RATE_LIMIT_WINDOW_S` | `10` | length of that window in seconds |
| `EHDS_RATE_LIMIT_BLOCK_S` | `60` | how long a flooding IP stays blocked |
| `EHDS_TRUST_PROXY` | `0` | `1` takes the client IP from `X-Forwarded-For` (only behind your own reverse proxy) |

Note that uvicorn's own `--proxy-headers` (on by default) already rewrites the client address from
`X-Forwarded-For` for peers in `--forwarded-allow-ips` (default `127.0.0.1`). Without a reverse proxy
run it with `--no-proxy-headers`, otherwise a local client can dodge the limit by changing that header.
`scripts/flood.py` sends a burst from one address and checks the 429 / `Retry-After` behaviour
(`--spoof` tries the header trick, `--wait` checks the unblock).

## Languages

`language.localise` runs before anything else in `pipeline.run` and `suggest.suggest`. One
OpenRouter call returns the ISO 639-1 code (enum of the 24 official EU languages) and the
English text of organisation, purpose, question and definitions; name, country, `requested_as`
and `requested_format` are never touched. `en` → the original request object is returned
unchanged. Otherwise the translation must keep every ICD-10/ATC/LOINC code, number and date
(numeric dates compare by day and year) and the number of definitions; one retry with the
problems, then the translation is used with `translation_unverified` and `needs_review`. The
result carries `language`, `translated` (the English text) and the flag `translated_from:<code>`.

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
  app, never patient data. No local cache: every request calls the model afresh; consistency
  comes from the strict JSON schema, the enum of retrieved points and the verbatim-quote checks.
  Embeddings: local `nomic-embed-text` via Ollama.
- Evaluation set: `tests/fixtures/eval/requests.json` (100 requests, tune on `dev`, report `test`).

## Data extraction

![From English to SQL](../docs/english-to-sql.svg)

- Each definition must fully match a regex rule; the report line is read via the defined names.
  Anything not understood raises `InterpretError`, never skipped.
- Where the rules give up, the OpenRouter LLM (same model as the decision, request text only,
  never patient data) fills the same `QuerySpec`; same checks, codes must come from the request,
  result flagged `llm_interpreted`, no local cache.
- One patient-ID CTE per concept, no correlated subqueries; rounding and small cells in Python.
- Black-box set in `tests/fixtures/blackbox/`, written by a separate agent; `run.py --verbose ID` shows one case.
