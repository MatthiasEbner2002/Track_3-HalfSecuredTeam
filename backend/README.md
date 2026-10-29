# Backend

FastAPI backend of the EHDS Health Data Access Body. **Skeleton only:** it starts,
answers `/health` and exposes the API shape; every `/api` route returns
`501 Not implemented yet` and every service raises `NotImplementedError`.

## Run

```bash
cd backend && uv sync && uv run uvicorn app.main:app --reload --port 8000
```

- Health check: http://localhost:8000/health
- OpenAPI docs: http://localhost:8000/docs

## Test

```bash
uv run pytest
```

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `EHDS_DATA_DIR` | `../data` | Folder with the five CSV files (relative to `backend/`) |
| `EHDS_SPE_DIR` | `./spe` | Where secure processing environments are created (git-ignored) |
| `EHDS_CORS_ORIGINS` | `http://localhost:5173,http://localhost:3000` | Comma-separated allowed frontend origins |

Fixed in `app/config.py`: reference date `2025-12-31`, small-cell threshold `10`.

## Layout

```
backend/
├── app/
│   ├── main.py            FastAPI app, lifespan, CORS, /health
│   ├── config.py          Settings from environment variables
│   ├── api/routes.py      /api routes (stubs)
│   ├── models/schemas.py  Request and decision models
│   └── services/
│       ├── vocab.py       Plain English -> ICD-10 / ATC / LOINC prefixes
│       ├── interpret.py   Request definitions -> QuerySpec
│       ├── engine.py      QuerySpec -> SQL -> raw result table
│       ├── disclosure.py  Small-cell rule and rounding
│       ├── decide.py      REJECT / APPROVE_STATISTICAL / APPROVE_SPE + articles
│       └── spe.py         SPE provisioning, download block, audit log
└── tests/
```
