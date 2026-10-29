---
type: uebergabe
title: EHDS Track 3 – from request definitions to SQL
created: 2026-10-29
session: session_0132ccQYsUKs4d1dvr5xZzMT
---
# Handover: from request definitions to SQL

Scope: only the data extraction after the decision is made. Decision logic, scaffold and tooling are
out of scope.


## Which request fields change the data
| Field | Effect |
|---|---|
| `definitions` | Main source: concepts with codes, age bands, active-drug rule, latest measurement, "Report per ..." line with grouping and metrics |
| `requested_as` + decision | Output mode: aggregates or SPE records. A downgraded access application (R09) gets statistics built from its "Report per" line |
| `question` | Sometimes: R05 names the record types to copy only here; R03 states "aged 65 and over" |
| `purpose`, `requested_format`, `applicant`, `id`, `submitted` | No effect on the data |
Always applied by the engine, never parsed: opt-out exclusion, reference date 2025-12-31, small-cell rule.

## Pipeline
1. Parse definitions (+ question) into a QuerySpec. Never generate SQL directly from text.
2. Resolve concepts to codes (ICD-10, ATC, LOINC).
3. Cohort = base view (not opted out, age and band computed) + filters.
4. One derived value or yes/no flag per patient (latest BMI, active ARB, ...).
5. Statistics: group, compute metrics, left-join the full group list, small-cell rule.
   SPE: take cohort IDs, copy only the named record types, write the exact count to the manifest.

## No hardcoding per request
The jury sends new requests, and the README forbids per-request code. Wording varies, the data does not:
the QuerySpec must cover what the data allows, not just the 10 samples (main diagnosis, group by hospital
or sex, two group dimensions, AND/OR/NOT of conditions, measurement thresholds, date windows, encounter
type, stays vs patients). Write 10-15 own test requests with numbers computed by a direct script.

## Rules first, AI later
Rule parser and LLM must both output the same QuerySpec (pydantic model = the contract). Stage 1 rules
only, with loud failure on any sentence not understood (never skip silently). Stage 2 the LLM fills the
same QuerySpec where rules fail, same validation. Optional stage 3: LLM first, rules as a cross-check.

## Regex rules
Ordered pattern table, each pattern must match the whole sentence (`fullmatch`), named groups extract
name, codes, bands, LOINC, year. Sentences with two definitions joined by ";" are split first. No match =
"could not interpret: <sentence>". Demo: `docs/rules_demo.py` (run with the path to `requests/`).
Result on the 10 samples: 41 of 41 sentences matched. Concept definitions parse cleanly. The hard part
is the free English that a match still returns: the metric list after "Report per ...:" and R05's
"Cohort = ..." expression.

## Resolving metrics and columns
Columns are never found by matching words to column names. Each concept kind has a fixed binding,
written once for the data:

| Kind | Table | Columns |
|---|---|---|
| Diagnosis | conditions | `icd10` starts with prefix |
| Drug | medications | `atc` prefix, `start_date`/`end_date` for "active" |
| Measurement | observations | `loinc` = code, `value`, `date` for "latest" |
| Main diagnosis of a stay | encounters | `primary_icd10` prefix |
| Visit type / year | encounters | `type`, `start_date` |
| Age, age band | patients | 2025 minus birth year of `birth_date` |
| Sex / region / hospital | patients | `sex` / `region` / `data_holder` |
| Opt-out | patients | `opted_out`, always applied |

Group words: region -> `region`, band / age band -> computed band, hospital / data holder ->
`data_holder`, sex -> `sex`. Checked: in all 47,706 encounters the encounter's `data_holder` equals the
patient's, so `patients.data_holder` works for stays and patients alike.

Each definition creates a named concept with its column attached; each concept becomes a yes/no flag per
patient. For the "Report per" line:
1. Split at commas and "and".
2. Find the defined concept names in each part.
3. Read the operation from keywords: "how many" / "number of" / bare name = count; "%", "share",
   "prevalence" = percentage; "mean", "average" = mean; "no", "without" = negate the flag;
   "who have", "with" = require the concept.
4. The operation picks the column: count = distinct `patient_id` (or `encounter_id` when the unit is
   stays); percentage = flagged count / group count x 100; mean = `observations.value` of the newest row
   per patient (only measurements have a value, so "mean" on anything else is an error).
5. The cohort is the denominator unless the phrase names another one (R01: all adults in the region).

Worked example R09: report line "adults with hypertension, % with an active ACE inhibitor, % with an
active ARB" -> cohort = adult AND hypertension, count; ACE inhibitor flag, percentage; ARB flag,
percentage; group by band 18-49 / 50-64 / 65+.

```sql
WITH base AS (
  SELECT patient_id, region, data_holder, sex,
         2025 - CAST(substr(birth_date, 1, 4) AS INTEGER) AS age
  FROM patients WHERE opted_out = 0),
cohort AS (
  SELECT * FROM base b WHERE age >= 18
    AND EXISTS (SELECT 1 FROM conditions c
                WHERE c.patient_id = b.patient_id AND c.icd10 LIKE 'I10%')),
flagged AS (
  SELECT k.*,
    EXISTS (SELECT 1 FROM medications m WHERE m.patient_id = k.patient_id
            AND m.atc LIKE 'C09AA%' AND m.start_date <= '2025-12-31'
            AND (m.end_date IS NULL OR m.end_date >= '2025-12-31')) AS ace,
    EXISTS (SELECT 1 FROM medications m WHERE m.patient_id = k.patient_id
            AND m.atc LIKE 'C09CA%' AND m.start_date <= '2025-12-31'
            AND (m.end_date IS NULL OR m.end_date >= '2025-12-31')) AS arb
  FROM cohort k)
SELECT CASE WHEN age <= 49 THEN '18-49' WHEN age <= 64 THEN '50-64' ELSE '65+' END AS band,
       count(*) AS n, sum(ace) AS ace_n, sum(arb) AS arb_n
FROM flagged GROUP BY band
```
Python then computes the percentages (Decimal, half up), applies the small-cell rule and joins in the
full band list so empty bands appear.

Verified on data/ehds.db (SQLite): 18-49 359 / 39.6 / 33.4, 50-64 1057 / 41.7 / 32.4,
65+ 2046 / 40.2 / 33.2, identical to expected.json.

SQLite notes:
- No `year()`: age = 2025 minus `CAST(substr(birth_date, 1, 4) AS INTEGER)`.
- Booleans are 0/1: `opted_out = 0`; `EXISTS` returns 0/1, so `sum(flag)` needs no cast.
- Dates are ISO text, so `<=` / `>=` compare correctly.
- An empty `end_date` must be stored as NULL (the current import does this: 8,917 open prescriptions).
  If the db is rebuilt from the CSVs with empty strings, every running drug counts as inactive.
- `LIKE` ignores case for ASCII letters; harmless here because all codes are uppercase.
- Never round in SQL: SQLite `round()` works on floats and can round .x5 the wrong way.

Stop with a clear message (later: hand to the LLM) when: a metric part has no known concept name; the
operation does not fit the concept ("mean of hypertension"); a group word is not in the fixed list
("per district"); a code prefix matches nothing in the data.

## Locations
- Data and requests: data/ and requests/ in this repo (copies of ~/Github/TRACK-3-ehds), expected
  answers in requests/expected.json.
- Pipeline: backend/app/pipeline.py and backend/app/layers/ (mapping, query, statistics, small_cell, spe).
- Regex demo: docs/rules_demo.py.
