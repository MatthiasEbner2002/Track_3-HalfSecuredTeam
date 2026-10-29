# Track 3 — EHDS Health Data Access Body (HalfSecuredTeam)

An app that takes a health-data request as JSON, decides `REJECT`, `APPROVE_STATISTICAL`
or `APPROVE_SPE` with the articles of Regulation (EU) 2025/327, and computes the
answer from 20,000 synthetic patients under the small-cell rule. Task: `Task_desc.md`.

Setup, running and tests: [`backend/README.md`](backend/README.md).

```bash
cd backend && .venv/bin/uvicorn app.main:app --port 8000
curl -X POST localhost:8000/api/requests -H 'Content-Type: application/json' -d @../requests/R01.json
```

## Architecture

```
request JSON ─▶ decision (purpose check) ─▶ routing ─▶ data extraction ─▶ answer + reason
                 Art. 54 / 53, rules +        statistics   rules, then IBM Bob,
                 optional local NLI model     or SPE       QuerySpec → SQLite
```

| Component | Where | How |
|---|---|---|
| Decision | `backend/app/layers/decision.py`, `backend/app/purpose/` | Rules for clear cases; optional local NLI model scores the purpose against each point of Art. 54 / 53 |
| Routing | `backend/app/layers/routing.py` | Statistics, SPE, or an access application downgraded to statistics (R09) |
| Data extraction | `backend/app/layers/mapping.py`, `query.py`, `llm_mapping.py` | See below |
| Small-cell rule | `backend/app/layers/small_cell.py` | `<10`, `suppressed`, one decimal half up, every group listed |
| API | `backend/app/main.py` | FastAPI, `POST /api/requests` |

![Data extraction: request text to numbers](docs/data-extraction.svg)

The request's definitions become a `QuerySpec` (a pydantic model). A regex rule parser
fills it; where it cannot read a sentence, **IBM Bob** (`bob` CLI, headless, request text
only, never patient data) fills the same model and passes the same checks. A compiler
turns the `QuerySpec` into SQLite SQL. Nothing is written per request.

## Checkpoint — 29.10.2026

### Metrics on the 10 requests (through `pipeline.run`, what the API returns)

| Metric | Target | Now |
|---|---|---|
| Correct decisions | 10 of 10 | **10 of 10** |
| Correct numbers | 6 of 6 | **6 of 6** |
| Privacy leaks | 0 | **0** |
| Consistency (3 runs) | 10 of 10 | **10 of 10** |
| Slowest answer | under 60 s | **0.2 s** (up to ~20 s when Bob is needed) |

Measured with the rules only for the decision (local NLI model not installed).

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
   disguised prohibited purposes are the weak spot of rules and the NLI model.
5. **Independent test set** written by a teammate, for an honest number for the jury.
6. **Submission**: 2-minute demo video, metric results, this README kept up to date.
