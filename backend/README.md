# Backend

The request pipeline. Each layer is one module with a typed input and output,
so it can be replaced on its own.

```
Request -> decision -> REJECT (articles)
              └ accept -> routing -> statistics | spe        -> ResultSpec ("result needed")
                                  -> mapping                 -> CodeMapping (ICD-10 / ATC / LOINC)
                                  -> query                   -> QueryPlan (SQL)
                                  -> db.execute              -> rows
                                  -> small_cell              -> rows
                                  -> PipelineResult
```

| Layer | File | State |
|---|---|---|
| Decision system | `app/layers/decision.py` | grounded RAG with a local LLM ([below](#decision-layer-grounded-rag)) |
| Statistics vs SPE | `app/layers/routing.py` | RAG check of Art. 66(3) with a verified quote; adds Art. 66/68/69/73 |
| Statistics spec | `app/layers/statistics.py` | stub, passes request through |
| SPE spec | `app/layers/spe.py` | stub, asks for all tables |
| Code mapping | `app/layers/mapping.py` | rule parser, IBM Bob fallback ([data extraction](#data-extraction-mapping--query-layers)) |
| Query generation | `app/layers/query.py` | QuerySpec compiled to SQL |
| Small-cell rule | `app/layers/small_cell.py` | applied in `query.py` |

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
   quotes the matched point verbatim. Articles: `54` on reject, `53` on accept.
5. **Routing** (`app/layers/routing.py`): a health data request gets statistics
   (`69`). For an access application, Art. 66(2) and 66(3) are fetched from the store
   and the LLM decides whether the application shows that its purpose cannot be met
   with anonymised statistics, quoting the sentence that says so. Quote found in the
   request → SPE (`66`, `68`, `73`); otherwise downgrade to statistics (`66`, `69`),
   flagged `spe_quote_unverified` when the model claimed a justification it could not quote.

LLM: `qwen2.5:7b` via Ollama, temperature 0, fixed seed. Only the request text
reaches it, never patient data. Decisions and routes are cached per request content,
so a resubmitted request gets the identical answer instantly. If Ollama is down the API
answers 503 with how to fix it. Recitals are in the store but not used yet.

Full decision (decision + route + articles) on the 10 task requests and the evaluation set:

```bash
.venv/bin/python scripts/evaluate.py --set tasks --runs 3
.venv/bin/python scripts/evaluate.py --split test --failures
```

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

## Purpose check (not used by the pipeline)

`app/purpose/` is an earlier, standalone purpose check (IBM Bob / NLI model / rules).
The pipeline no longer calls it; decisions and routing use the RAG engine above.

Three engines, tried in order (`engines` in `app/purpose/config.yaml`); the first
one that is available and answers decides:

1. **IBM Bob** (`bob` command, npm `@ibm/bob`, needs `BOB_API_KEY`): a cloud language
   model judges every point of Art. 54(1) and 53(1) and whether patient-level data is
   justified, and quotes the request. Only request text is sent, never patient data.
   Bob is a coding agent, so each call runs in a fresh empty folder with every tool
   group disabled and one turn. The answer is validated (known points only, quotes
   must occur in the request) and cached in `backend/.cache/purpose-llm.json`, so the
   same request always gets the same decision. About 5 s per request.
2. **Local NLI model**
   ([`MoritzLaurer/bge-m3-zeroshot-v2.0`](https://huggingface.co/MoritzLaurer/bge-m3-zeroshot-v2.0),
   multilingual, offline on CPU) scores one statement per article point. Used when Bob
   is not installed or fails (flag `llm_failed`). About 7 s per request.
3. **Rules**: a few unambiguous phrases (flag `rules_only`).

The rule floor always applies on top: a rule hit rejects whatever the engine said.
Every decision quotes the sentence that led to it (`evidence`). Statements, thresholds,
rules and reason texts live in `app/purpose/config.yaml`.

Choose engines with `EHDS_PURPOSE_ENGINES=nli,rules` (no cloud) or
`EHDS_PURPOSE_RULES_ONLY=1` (no models, fast tests).

Install the local model libraries (CPU build of torch first, ~1.2 GB model download on first use):

```bash
.venv/bin/pip install torch --index-url https://download.pytorch.org/whl/cpu
.venv/bin/pip install -e ".[dev,nli]"
```

Fast tests without the model: `EHDS_PURPOSE_RULES_ONLY=1 .venv/bin/pytest -q`

### Evaluation set

`tests/fixtures/eval/requests.json` holds 100 labelled requests (40 rejects over all
Art. 54 points, 35 statistical approvals, 13 SPE approvals, 12 downgrades, 10 in
German), split into `dev` (64) and `test` (36). Build it with
`scripts/build_eval_set.py`, score it with `scripts/evaluate.py`.

Protocol: the statements and thresholds are set from the law and `requests/R01-R10`
only, never from the evaluation set. Report the `test` split; tune on `dev` only.
The set was written by the same person who wrote the purpose check, so a set
written by someone else is the stronger test.

## Data extraction (mapping + query layers)

![Data extraction: request text to numbers](../docs/data-extraction.svg)

After an approval, `app/layers/query.py:interpret` turns the request into a
`QuerySpec` (`app/models.py`), the contract between interpretation and SQL:

1. **Rules** (`app/layers/mapping.py`): each definition sentence must fully match a
   regex rule; the "Report per ..." line is read by finding the defined concept names.
   Anything not understood raises `InterpretError`, never skipped silently.
2. **IBM Bob** (`app/layers/llm_mapping.py`), only where the rules give up: `bob run
   --mode ask` in an empty folder gets the request text (never patient data), the data
   bindings and worked examples, and must return a `QuerySpec` that passes the same
   validation and code check (one retry, then `InterpretError`). Answers are cached in
   `backend/.cache/llm/`, so reruns are identical and free. The pipeline flags such
   results `llm_interpreted` for staff review. Needs the `bob` CLI and `BOB_API_KEY`;
   off with `EHDS_LLM=off` (the unit tests set this). A code that matches nothing in
   the data (`NoDataError`) never goes to Bob.

`compile_spec` turns the spec into SQLite SQL (one patient-ID CTE per concept, no
correlated subqueries); percentages and means are rounded half up in Python, then
the small-cell rule applies.

Black-box set (written by a separate agent, not read while developing the parser):

```bash
.venv/bin/python tests/fixtures/blackbox/run.py            # PASS/FAIL lines only
.venv/bin/python tests/fixtures/blackbox/run.py --verbose B048   # one case in full
```
