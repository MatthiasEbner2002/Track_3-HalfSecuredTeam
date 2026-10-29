# Backend

Skeleton of the request pipeline. Every layer is a stub that passes data through,
so each one can be replaced on its own.

```
Request -> decision -> REJECT (articles)
              └ accept -> routing -> statistics | spe        -> ResultSpec ("result needed")
                                  -> mapping                 -> CodeMapping (ICD-10 / ATC / LOINC)
                                  -> query                   -> QueryPlan (SQL)
                                  -> db.execute              -> rows
                                  -> small_cell              -> rows
                                  -> PipelineResult
```

| Layer | File | Stub behaviour |
|---|---|---|
| Decision system | `app/layers/decision.py` | **real**: grounded RAG + local LLM, see below |
| Statistics vs SPE | `app/layers/routing.py` | always `statistics` |
| Statistics spec | `app/layers/statistics.py` | passes request through |
| SPE spec | `app/layers/spe.py` | asks for all tables |
| Code mapping | `app/layers/mapping.py` | empty mapping |
| Query generation | `app/layers/query.py` | `SELECT *` from every table |
| Small-cell rule | `app/layers/small_cell.py` | returns rows unchanged |

The interfaces between layers live in `app/models.py`; `app/pipeline.py` wires them.

## Decision layer (grounded RAG)

1. **Corpus** (`app/rag/corpus.py`): parses `eulex.html` (Regulation (EU) 2025/327,
   saved from EUR-Lex) into 406 chunks: each paragraph of Chapter IV (Art. 50–81),
   each lettered point on its own (`Art. 54(b)`, `Art. 53(1)(e)`, sub-points stay
   inside), and the 115 recitals.
2. **Store** (`app/rag/store.py`): each chunk is embedded with `nomic-embed-text`
   and stored in `data/law.db` (SQLite, committed). Retrieval is cosine similarity.
3. **Decision** (`app/layers/decision.py`), three steps:
   1. *Extract* (LLM): actor, analysis, **end use** and affected people, from the
      organisation and purpose only. The question is always a harmless statistic;
      the intent is in what happens with the result afterwards.
   2. *Retrieve*: the end use is the query; it picks the 3 closest points of
      Art. 53(1) (permitted) and of Art. 54 (prohibited).
   3. *Match* (LLM): one choice among those 6 points, permitted and prohibited
      competing, plus the words of the point that cover the use. If those words are
      not in the law text, `needs_review` is set for staff.
4. **Rules** in code: an Art. 54 point rejects, an Art. 53 point accepts. The reason
   quotes the matched point verbatim. Articles: `54` on reject, `53` on accept,
   `66` for an access application; routing adds `69` (statistics) or `68`, `73` (SPE).

LLM: `qwen2.5:7b` via Ollama, temperature 0, fixed seed. Only the request text
reaches it, never patient data. Recitals are in the store but not used yet.

```bash
brew install ollama && ollama serve &
ollama pull qwen2.5:7b && ollama pull nomic-embed-text
.venv/bin/python -m app.rag.corpus        # rebuild data/law.db after changing eulex.html
.venv/bin/python -m app.eval_decisions    # decisions on the 10 requests + 16 held-out ones
```

## Setup

```bash
cd backend
python3.13 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

## Database

`data/ehds.db` (SQLite) is built from the CSVs in `data/` and committed to git.
Rebuild after changing a CSV:

```bash
.venv/bin/python -m app.db
```

## Run

```bash
.venv/bin/uvicorn app.main:app --reload --port 8000
curl -X POST localhost:8000/api/requests -H 'Content-Type: application/json' -d @../requests/R01.json
```

## Tests

```bash
.venv/bin/pytest -q -rxX
```

`tests/test_expected.py` compares against `requests/expected.json` and is marked
`xfail` while the decision layer is a stub; `XPASS` lines show what already works.
