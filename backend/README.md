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
