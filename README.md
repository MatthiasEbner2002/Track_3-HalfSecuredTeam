# Track 3 — EHDS Health Data Access Body (HalfSecuredTeam)

Takes a health-data request as JSON, decides `REJECT`, `APPROVE_STATISTICAL` or `APPROVE_SPE`
with the articles of Regulation (EU) 2025/327 and computes the answer under the small-cell rule.
Task: [`Task_desc.md`](Task_desc.md). Details: [`backend/README.md`](backend/README.md).

## Status — 30.10.2026

Done:

- [x] Decision, all four steps, RAG only (no IBM Bob, no NLI model): Art. 54 / 53 point picked
  by the local LLM from retrieved law text and quoted; SPE vs. downgrade judged against
  Art. 66(2)/66(3) with a quote that must appear verbatim in the request (otherwise downgrade
  + staff flag); route articles (69, 66, 68, 73) grounded in the law text; decisions cached per
  request; `503` with a fix-it message when Ollama is down
- [x] Data extraction from the definitions: 6/6 numbers, black-box 100/100 with the rules alone
  (hospital grouping, main diagnosis `primary_icd10`, stays vs. patients covered)
- [x] Small-cell rule and opt-outs in every statistical answer
- [x] SPE cohort: exact patient count (R05: 1,152) and the requested record types
- [x] Frontend removed; only the JSON API exists

To do, in priority order:

1. [ ] **SPE provisioning**: copy the selected patients' requested record types (with their
   patient IDs) into a separate schema or folder per request, manifest with the exact count
2. [ ] **Download block**: an endpoint that refuses patient-level downloads from the SPE
   (Art. 73), shown in the demo
3. [ ] **Applicant and staff views**: decision, reason, articles for the applicant; evidence,
   grounding, flags (`needs_review`, `spe_quote_unverified`), SQL and code mapping for staff
   (minimal UI or a documented API flow)
4. [ ] **`/api/submissions`**: return `pipeline.run` instead of placeholder results, or remove it
5. [ ] **Step 1 hardening** (misses on the held-out set, below): deterministic Art. 54 floor for
   unambiguous wording (nicotine, vape, e-liquid, tobacco, alcohol products, also German) and a
   tighter end-use prompt; tune on the `dev` split only
6. [ ] **Data-extraction fallback without Bob**: `llm_mapping.py` still calls the `bob` CLI, which
   is not installed, so a sentence the rules cannot read gives no numbers; fill the same
   `QuerySpec` with the local Ollama model instead
7. [ ] **Cleanup**: delete or archive the unused `app/purpose/` and the stubs
   `layers/statistics.py`, `layers/spe.py`, `small_cell.apply`
8. [ ] **Persistence**: submissions and SPE environments survive a restart
9. [ ] **Submission**: rerun all metrics, final architecture section, public repo, demo video
   (≤ 2 min: R02 reject, R01 statistics, R05 SPE with the blocked download)

## Quick start

Needs Python 3.12+ and [Ollama](https://ollama.com/download) (~6 GB disk, 8 GB RAM).

```bash
ollama serve &   # or the Ollama desktop app
ollama pull qwen2.5:7b && ollama pull nomic-embed-text
cd backend && python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/uvicorn app.main:app --port 8000
curl -X POST localhost:8000/api/requests -H 'Content-Type: application/json' -d @../requests/R02.json
.venv/bin/pytest -m "not real_llm"
```

API docs: <http://localhost:8000/docs>. A `503` means Ollama is not running or a model is missing.

## Architecture

```
request ─▶ decision ──────▶ routing ──────────────────▶ data extraction ─▶ answer + reason
           Art. 54 / 53:     statistics (Art. 69),       rules, then IBM Bob,
           RAG + Ollama      SPE (Art. 66(3), 68, 73)    QuerySpec → SQLite
                             or downgrade (66(2), 69):
                             RAG + Ollama
```

![Data extraction: request text to numbers](docs/data-extraction.svg)

## Metrics

On the 10 requests, through `pipeline.run` (what the API returns), real LLM, cache cleared:

| Metric | Target | Now |
|---|---|---|
| Correct decisions | 10 of 10 | 10 of 10 (decision + articles) |
| Correct numbers | 6 of 6 | 6 of 6 |
| Privacy leaks | 0 | 0 |
| Consistency (3 runs) | 10 of 10 | 10 of 10 (uncached model calls) |
| Slowest answer | under 60 s | 13.9 s (`qwen2.5:7b`) |

Held-out evaluation set, `test` split (36 requests, `scripts/evaluate.py --split test`):
31 of 36 full decisions correct; routing SPE 4/5, downgrade 4/4. Misses: three prohibited
purposes accepted (two nicotine/vape products, one in German, and a subsidy cut that
disadvantages a group), one study *about* insurance premiums rejected, one SPE request with a
bare "requires patient-level data" downgraded.
