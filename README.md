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
python -m app.eval_decisions        # decisions on the 10 task requests + 16 held-out ones (~3 min)
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
| `EHDS_DECISION_ENGINE` | `rag` | `purpose` switches to the purpose check (NLI model / IBM Bob / rules) |
| `EHDS_LLM_MODEL` | `qwen2.5:7b` | e.g. `qwen2.5:3b` on a small laptop (less accurate) |
| `EHDS_EMBED_MODEL` | `nomic-embed-text` | rebuild `law.db` after changing it |
| `EHDS_OLLAMA_URL` | `http://localhost:11434` | Ollama on another machine |

## Troubleshooting

- **A request returns `Internal Server Error`**: look at the uvicorn log.
  `Connection refused` means Ollama is not running (`ollama serve`);
  `HTTP Error 404` means a model is missing (run the `ollama pull` commands from step 2).
- **Real-LLM tests are skipped**: same cause, Ollama or a model is missing.

## Architecture

```
request JSON ─▶ decision ─────────────────▶ routing ─▶ data extraction ─▶ answer + reason
                 Art. 54 / 53:                statistics   rules, then IBM Bob,
                 RAG + local LLM (default)    or SPE       QuerySpec → SQLite
                 or purpose check
```

| Component | Where | How |
|---|---|---|
| Decision | `backend/app/layers/decision.py` | Two engines, `EHDS_DECISION_ENGINE`: **`rag`** (default) |
| ↳ RAG engine | `backend/app/rag/` | Regulation parsed into points and recitals, embedded in `data/law.db`; a local LLM (Ollama `qwen2.5:7b`) extracts the end use, picks the matching Art. 53/54 point and the reason quotes it verbatim |
| ↳ Purpose check | `backend/app/purpose/` | `purpose`: rules for clear cases; optional local NLI model scores the purpose against each point of Art. 54 / 53 |
| Routing | `backend/app/layers/routing.py` | Statistics, SPE, or an access application downgraded to statistics (R09) |
| Data extraction | `backend/app/layers/mapping.py`, `query.py`, `llm_mapping.py` | See below |
| Small-cell rule | `backend/app/layers/small_cell.py` | `<10`, `suppressed`, one decimal half up, every group listed |
| API | `backend/app/main.py` | FastAPI, `POST /api/requests` |

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

## Checkpoint — 29.10.2026

### Metrics on the 10 requests (through `pipeline.run`, what the API returns)

| Metric | Target | Now |
|---|---|---|
| Correct decisions | 10 of 10 | **10 of 10** |
| Correct numbers | 6 of 6 | **6 of 6** |
| Privacy leaks | 0 | **0** |
| Consistency (3 runs) | 10 of 10 | **10 of 10** |
| Slowest answer | under 60 s | **0.2 s** (up to ~20 s when Bob is needed) |

Measured with the purpose check, rules only (local NLI model not installed).
The RAG engine (default) also gets 10 of 10 decisions, plus 16 of 16 on held-out requests
(`python -m app.eval_decisions`), consistent over 3 runs; with `qwen2.5:7b` a decision takes
5–10 s (~15 s for the first request while the model loads).

### Done

- **Decision and routing**: Art. 54 rejects, statistics, SPE with justification, downgrade
  of access applications; evidence quotes and staff flags (`needs_review`, `llm_interpreted`).
- **Data extraction**: `QuerySpec` contract, rule parser, SQL compiler, IBM Bob as stage 2
  with validation, codes only from the request, answers cached for identical reruns.
- **Small-cell rule and opt-outs** applied in the answer the API returns.
- **Tests**: 79 unit tests; black-box set of 100 new requests written by a separate agent
  (`backend/tests/fixtures/blackbox/`): **83/100 with rules only, 100/100 with Bob**.
- **Evaluation set for decisions**: 100 labelled requests in `backend/tests/fixtures/eval/`.

### Still to do

1. **SPE copy**: copy the selected patients' named record types into a separate schema or
   folder, keep their IDs, and show a blocked download. Today only the exact cohort size
   and the record types are returned (`backend/app/layers/spe.py` is still a stub).
2. **What applicants and staff see**: a frontend or at least readable pages for the
   decision, the reason and the numbers. Only the JSON API exists.
3. **Rule parser gaps** (Bob covers them today; rules would be faster and free), from the
   17 black-box requests the rules could not read:
   - a definition built from other definitions (`X = patient with A and B`,
     `patient with at least one inpatient stay in 2025`, `patient whose region is 'Wien'`)
   - stays counted for a sub-population (`Count visits (not patients) of patients with asthma`)
   - "patients in the cohort" in the report line
   - short names for measurements (`a systolic value`)
   - a date after an inline age (`aged 40 or older on 31 December 2025`)
   - more wordings: `is identified by …`, `the most recent value of LOINC …`,
     `start_date falls in 2025`, `regardless of start or end date`
4. **Decision quality on new requests**: score the held-out split of the evaluation set;
   disguised prohibited purposes are the weak spot of rules and the NLI model. Compare
   both engines on it and pick one (or combine them).
5. **Independent test set** written by a teammate, for an honest number for the jury.
6. **Submission**: 2-minute demo video, metric results, this README kept up to date.
