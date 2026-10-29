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
| Decision system | `app/layers/decision.py` | accepts everything |
| Statistics vs SPE | `app/layers/routing.py` | always `statistics` |
| Statistics spec | `app/layers/statistics.py` | passes request through |
| SPE spec | `app/layers/spe.py` | asks for all tables |
| Code mapping | `app/layers/mapping.py` | empty mapping |
| Query generation | `app/layers/query.py` | `SELECT *` from every table |
| Small-cell rule | `app/layers/small_cell.py` | returns rows unchanged |

The interfaces between layers live in `app/models.py`; `app/pipeline.py` wires them.

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

## Purpose check (decision + routing layers)

`app/purpose/` decides which article points apply to a request. It is used by
`app/layers/decision.py` (Art. 54 reject, Art. 53 accept) and
`app/layers/routing.py` (statistics or SPE, plus the route's articles).

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
