# Track 3 — EHDS Health Data Access Body (HalfSecuredTeam)

Takes a health-data request as JSON, decides `REJECT`, `APPROVE_STATISTICAL` or `APPROVE_SPE`
with the articles of Regulation (EU) 2025/327 and computes the answer under the small-cell rule.
Task: [`Task_desc.md`](Task_desc.md). Details: [`backend/README.md`](backend/README.md).

## Status

- [x] Decision and routing (Art. 54 rejects, statistics, SPE, downgrade): 10/10 on the samples
- [x] Data extraction from the definitions: 6/6 numbers, black-box 100/100 with the rules alone
- [x] IBM Bob as fallback for wordings the rules do not know
- [x] Small-cell rule and opt-outs in the API answer: 0 leaks
- [ ] Fix the 10 failing `test_purpose.py` tests (the `rag` engine returns no evidence)
- [ ] Decision engines: compare `rag` and `purpose` on the held-out requests and pick one
- [ ] SPE: copy the selected records into a separate schema, block downloads
- [ ] Pages for applicants and staff (only the JSON API exists)
- [ ] Independent test set
- [ ] Demo video

## Quick start

Needs Python 3.12+ and [Ollama](https://ollama.com/download) (~6 GB disk, 8 GB RAM).

```bash
ollama pull qwen2.5:7b && ollama pull nomic-embed-text
cd backend && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/uvicorn app.main:app --port 8000
curl -X POST localhost:8000/api/requests -H 'Content-Type: application/json' -d @../requests/R02.json
.venv/bin/pytest -m "not real_llm"
```

## Architecture

```
request ─▶ decision ─▶ routing ─▶ data extraction ─▶ answer + reason
           RAG + Ollama   statistics  rules, then IBM Bob,
           or purpose     or SPE      QuerySpec → SQLite
```

![Data extraction: request text to numbers](docs/data-extraction.svg)

## Metrics on the 10 requests

| Metric | Target | Now |
|---|---|---|
| Correct decisions | 10 of 10 | 10 of 10 |
| Correct numbers | 6 of 6 | 6 of 6 |
| Privacy leaks | 0 | 0 |
| Consistency (3 runs) | 10 of 10 | 10 of 10 |
| Slowest answer | under 60 s | 5–10 s with `rag` (~15 s for the first request) |
