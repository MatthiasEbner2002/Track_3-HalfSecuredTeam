# Track 3 — EHDS Health Data Access Body (HalfSecuredTeam)

An app that takes a health-data request as JSON, decides `REJECT`, `APPROVE_STATISTICAL`
or `APPROVE_SPE` with the articles of Regulation (EU) 2025/327, and computes the
answer from 20,000 synthetic patients under the small-cell rule. Task: `Task_desc.md`.

## Getting started

### 1. Prerequisites

- **Python 3.12+** (`python3 --version`)
- **Ollama** for the local LLM: <https://ollama.com/download>
  (macOS: `brew install ollama`)
- About 6 GB of free disk space and 8 GB of RAM for the models

### 2. Start Ollama and download the models

```bash
ollama serve                    # keep this running in its own terminal (the desktop app does it for you)
ollama pull qwen2.5:7b          # LLM for decisions, ~4.7 GB
ollama pull nomic-embed-text    # embeddings for retrieval, ~270 MB
```

### 3. Install the backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate       # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

Both databases are committed, so there is nothing to build:
`data/ehds.db` (patients, from the CSVs) and `data/law.db` (the regulation, embedded).

### 4. Run

```bash
uvicorn app.main:app --reload --port 8000
```

API docs: <http://localhost:8000/docs>. Submit one of the example requests
(from the `backend` folder, in a second terminal):

```bash
curl -s -X POST localhost:8000/api/requests \
  -H 'Content-Type: application/json' -d @../requests/R02.json \
  | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['decision'], d['articles']); print(d['reason'])"
```

```
REJECT ['54']
Rejected: prohibited secondary use under Art. 54(b). Applicant: the pricing department of Alpenland Versicherung AG. ...
```

The first request takes ~15 s while Ollama loads the model; later ones take 5–10 s.

### 5. Test

```bash
pytest -m "not real_llm"            # fast unit tests, no Ollama needed (~5 s)
python -m app.eval_decisions        # accept/reject on the 10 task requests + 16 held-out ones (~3 min)
python scripts/evaluate.py --set tasks --runs 3   # full decision + articles on R01-R10, 3 runs
python scripts/evaluate.py --split test --failures # full decision on the 36 held-out eval requests
pytest                              # everything, including the real-LLM tests (slow)
```

## Rebuilding the databases

Only needed after changing the CSVs in `data/` or `eulex.html`:

```bash
python -m app.db             # data/ehds.db from data/*.csv
python -m app.rag.corpus     # data/law.db from eulex.html (needs Ollama)
```

## Configuration

Environment variables, all optional:

| Variable | Default | Use |
|---|---|---|
| `EHDS_LLM_MODEL` | `qwen2.5:7b` | e.g. `qwen2.5:3b` on a small laptop (less accurate) |
| `EHDS_EMBED_MODEL` | `nomic-embed-text` | rebuild `law.db` after changing it |
| `EHDS_OLLAMA_URL` | `http://localhost:11434` | Ollama on another machine |

## Troubleshooting

- **A request returns `503`**: the message says what is wrong. Ollama is not running
  (`ollama serve`) or a model is missing (run the `ollama pull` commands from step 2).
- **Real-LLM tests are skipped**: same cause, Ollama or a model is missing.

## Architecture

```
request JSON ─▶ decision ─────────────▶ routing ───────────────▶ data extraction ─▶ answer + reason
                 Art. 54 / 53:            statistics (Art. 69),    rules, then IBM Bob,
                 RAG + local LLM          SPE (Art. 66(3), 68, 73) QuerySpec → SQLite
                                          or downgrade (66(2), 69):
                                          RAG + local LLM
```

| Component | Where | How |
|---|---|---|
| Decision (steps 1–2) | `backend/app/layers/decision.py`, `backend/app/rag/` | Regulation parsed into points and recitals, embedded in `data/law.db`; a local LLM (Ollama `qwen2.5:7b`) extracts the end use, picks the matching Art. 53/54 point and the reason quotes it verbatim |
| Routing (steps 3–4) | `backend/app/layers/routing.py` | Health data request → statistics (Art. 69). Access application → the local LLM reads Art. 66(2)/66(3) from `data/law.db` and judges whether the application shows that statistics are not enough; its quote must appear verbatim in the request, otherwise the result is downgraded to statistics and flagged for staff |
| Data extraction | `backend/app/layers/mapping.py`, `query.py`, `llm_mapping.py` | See below |
| Small-cell rule | `backend/app/layers/small_cell.py` | `<10`, `suppressed`, one decimal half up, every group listed |
| API | `backend/app/main.py`, `submissions.py` | FastAPI, `POST /api/requests` (pipeline); `/api/submissions` (list/view/download, still placeholder results) |

![Data extraction: request text to numbers](docs/data-extraction.svg)

The request's definitions become a `QuerySpec` (a pydantic model). A regex rule parser
fills it; where it cannot read a sentence, **IBM Bob** (`bob` CLI, headless, request text
only, never patient data) fills the same model and passes the same checks. A compiler
turns the `QuerySpec` into SQLite SQL. Nothing is written per request.

| Path | Content |
|---|---|
| `backend/` | FastAPI app, layers, tests ([backend/README.md](backend/README.md) has the details) |
| `data/` | synthetic patient CSVs, `ehds.db`, `law.db` |
| `requests/` | the 10 example requests and `expected.json` |
| `eulex.html` | Regulation (EU) 2025/327 as saved from EUR-Lex |
| `docs/` | diagrams and demos |

## Current state — 30.10.2026

### Metrics on the 10 requests (through `pipeline.run`, what the API returns)

| Metric | Target | Now |
|---|---|---|
| Correct decisions | 10 of 10 | **10 of 10** (decision + articles) |
| Correct numbers | 6 of 6 | **6 of 6** |
| Privacy leaks | 0 | **0** |
| Consistency (3 runs) | 10 of 10 | **10 of 10** (uncached model calls) |
| Slowest answer | under 60 s | **13.9 s** (RAG decision + routing + query, `qwen2.5:7b`, cache cleared) |

Held-out evaluation set, `test` split (36 requests, `scripts/evaluate.py --split test`):
**31 of 36** full decisions correct. Routing: SPE 4/5, downgrade 4/4. Misses: three
prohibited purposes accepted (two nicotine/vape products, one of them in German, and a
subsidy cut that disadvantages a group), one study *about* insurance premiums rejected,
one SPE request with a bare "requires patient-level data" downgraded.

### Done

- **Decision, all four steps, RAG only** (no IBM Bob, no NLI model in the decision path):
  Art. 54 / 53 point picked by the local LLM from retrieved law text and quoted in the
  reason; SPE vs. downgrade judged against Art. 66(2)/66(3) with a quote that must appear
  verbatim in the request (otherwise downgrade + staff flag). Route articles (69, 66, 68, 73)
  grounded in the law text. Decisions cached per request content. 503 with a fix-it
  message when Ollama is down.
- **Data extraction**: regex rule parser → `QuerySpec` → SQLite SQL; 6/6 numbers on the
  samples, 100/100 on the black-box set with the rules alone. Covers grouping by hospital,
  main diagnosis (`primary_icd10`) and stays vs. patients.
- **Small-cell rule and opt-outs** in every statistical answer: `<10`, `suppressed`, one
  decimal half up, every group listed, opted-out patients excluded in the base query.
- **SPE cohort**: exact patient count (R05: 1,152) and the requested record types.
- **Tests**: 102 fast tests on fakes; real-LLM tests and both eval scripts.
- The frontend was removed; only the JSON API exists.

### To do

1. **SPE provisioning**: copy the selected patients' requested record types (with their
   patient IDs) into a separate schema or folder per request, with a manifest holding the
   exact count.
2. **Download block**: an endpoint that refuses patient-level downloads from the SPE
   (Art. 73), shown in the demo.
3. **Applicant and staff views**: decision, reason and articles for the applicant;
   evidence, grounding, flags (`needs_review`, `spe_quote_unverified`), generated SQL and
   code mapping for staff. Either a new minimal UI or a documented API flow for the demo.
4. **`/api/submissions`**: return `pipeline.run` instead of placeholder results (or
   remove it if the demo uses `/api/requests` only).
5. **Step 1 hardening**: the misses above. Deterministic Art. 54 floor for unambiguous
   wording (nicotine, vape, e-liquid, tobacco, alcohol products, also in German) and a
   tighter end-use prompt; tune on the `dev` split only.
6. **Data-extraction fallback without Bob**: `llm_mapping.py` still calls the `bob` CLI,
   which is not installed, so a sentence the rules cannot read gives no numbers. Fill the
   same `QuerySpec` with the local Ollama model instead.
7. **Cleanup**: delete or archive the unused `app/purpose/` (Bob/NLI purpose check) and the
   stubs `layers/statistics.py`, `layers/spe.py`, `small_cell.apply`.
8. **Persistence**: submissions and SPE environments survive a restart.
9. **Submission**: rerun all metrics after the changes, final README architecture section,
   public repository, demo video (≤ 2 min: R02 reject, R01 statistics, R05 SPE with the
   blocked download).
