# Track 3: European Health Data Space – HalfSecuredTeam

A Health Data Access Body prototype: it takes a data request as JSON, decides
`REJECT`, `APPROVE_STATISTICAL` or `APPROVE_SPE` with the articles of
Regulation (EU) 2025/327, and (later) answers it from the synthetic data.
The task is described in [Task_desc.md](Task_desc.md).

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
Request ─► decision ─► REJECT (Art. 54, quoted reason)
              └► accept ─► routing ─► statistics | SPE ─► code mapping ─► query ─► small-cell rule ─► result
```

Each box is one module in `backend/app/layers/`. The **decision layer** is
implemented (grounded RAG over the regulation with a local LLM); the other layers
are stubs that pass all data through and are being replaced one at a time.
Details: [backend/README.md](backend/README.md).

| Path | Content |
|---|---|
| `backend/` | FastAPI app, layers, tests |
| `data/` | synthetic patient CSVs, `ehds.db`, `law.db` |
| `requests/` | the 10 example requests and `expected.json` |
| `eulex.html` | Regulation (EU) 2025/327 as saved from EUR-Lex |
