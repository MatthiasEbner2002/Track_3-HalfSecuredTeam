"""Suggest the plain-English definitions for a request the applicant is still writing.

Same gate as a real request: the purpose goes through the law check first
(Art. 54 / 53). A prohibited purpose gets the rejection and no definitions.
Otherwise the LLM drafts definitions in the wording of the 10 task requests,
restricted to what the data can express (ICD-10 / ATC prefixes, the four
LOINCs, regions, hospitals, age bands). The draft is then run through the
same parser as a real request, so the applicant learns whether the access
body can compute it automatically. Only request text reaches the LLM.
"""

import json
import sqlite3
from functools import cache
from typing import Any

from pydantic import BaseModel

from app.config import REQUESTS_DIR
from app.layers import decision, language, mapping, query
from app.layers.mapping import KNOWN_MEASUREMENTS
from app.models import DataRequest
from app.rag.openrouter import OpenRouterLLM

SCHEMA = {
    "type": "object",
    "properties": {
        "definitions": {"type": "array", "items": {"type": "string"},
                         "description": "3 to 7 definitions, one sentence each, last one the 'Report per ...' line"},
        "note": {"type": "string", "description": "one sentence to the applicant: what was assumed or cannot be answered with these data"},
    },
    "required": ["definitions", "note"],
    "additionalProperties": False,
}

INSTRUCTIONS = """You help an applicant finish a request to a Health Data Access Body (EHDS, Regulation (EU) 2025/327).
The applicant wrote a purpose and a question. Write the "definitions": short plain-English sentences that pin
down every term so the access body can compute the answer without asking back.

The data: 20,000 synthetic patients from three Austrian hospitals, reference date 31 December 2025.
- conditions: one row per diagnosis with an ICD-10 code. Name conditions as "any condition with an ICD-10 code starting with X".
- medications: ATC code, start_date, end_date (empty = still active on 31 December 2025). "Active" = end date empty or after the reference date.
- observations: only these measurements exist: {measurements}. Name them in plain English; the access body maps them.
- encounters: inpatient, outpatient or emergency; inpatient stays have a main diagnosis (primary_icd10); years 2023-2025.
- patients: sex (female/male), birth date (age = 2025 minus birth year), region ({regions}), hospital ({hospitals}), opt-out (handled automatically).

Style, copied from accepted requests:
- "Adult = aged 18 or older on 31 December 2025."
- "Type 2 diabetes = any condition with an ICD-10 code starting with E11."
- "Active prescription = a medication with an ATC code starting with A10BK whose end date is empty or after 31 December 2025."
- "Age bands: 65-74, 75-84, 85+ on 31 December 2025."
- For statistics the last line is "Report per <region | hospital | sex | age band>: <value 1>, <value 2> and <value 3>." with
  counts of patients, percentages (say numerator and denominator) or means of one measurement.
- For pseudonymised records: "Cohort = <who>." and "Report the size of the cohort you would make available."

Rules: define only what the question needs; use the codes the applicant gave; if a term cannot be expressed with these
data, say so in the note instead of inventing a code. Never mention patient-level examples. Keep each definition one sentence.

Examples of complete requests (question -> definitions):
{examples}
"""


class Suggestion(BaseModel):
    decision: str  # "ok" or "REJECT"
    articles: list[str] = []
    reason: str = ""
    definitions: list[str] = []
    note: str = ""
    computable: bool | None = None  # the parser could turn the draft into a query (None when not tried)
    language: str = "en"  # language the applicant wrote in; the draft is in English


@cache
def _examples() -> str:
    out = []
    for path in sorted(REQUESTS_DIR.glob("R*.json")):
        r = json.loads(path.read_text())
        out.append(json.dumps({"requested_as": r["requested_as"], "question": r["question"]}) + "\n-> " + json.dumps(r["definitions"]))
    return "\n\n".join(out)


def _vocab(conn: sqlite3.Connection) -> dict[str, str]:
    regions = [r for (r,) in conn.execute("SELECT DISTINCT region FROM patients ORDER BY region")]
    hospitals = [h for (h,) in conn.execute("SELECT DISTINCT data_holder FROM patients ORDER BY data_holder")]
    names = sorted({k for k in KNOWN_MEASUREMENTS}, key=len, reverse=True)
    return {"regions": ", ".join(regions), "hospitals": ", ".join(hospitals),
            "measurements": "HbA1c, BMI, systolic blood pressure, eGFR (also written as: " + ", ".join(names) + ")"}


def suggest(request: DataRequest, conn: sqlite3.Connection, llm: Any | None = None) -> Suggestion:
    request, lang = language.localise(request, conn)  # any EU language in, English draft out
    verdict = decision.decide(request)  # the same law check as a submitted request
    if not verdict.accepted:
        return Suggestion(decision="REJECT", articles=verdict.articles, reason=verdict.reason, language=lang.language)
    llm = llm or OpenRouterLLM()
    system = INSTRUCTIONS.format(examples=_examples(), **_vocab(conn))
    user = json.dumps({
        "applicant": request.applicant.model_dump(),
        "requested_as": request.requested_as,
        "purpose": request.purpose,
        "question": request.question,
        "definitions_so_far": request.definitions,
    }, ensure_ascii=False)
    answer = llm.complete_json(system, user, SCHEMA)
    definitions = [d.strip() for d in answer.get("definitions", []) if d and d.strip()]
    draft = request.model_copy(update={"definitions": definitions})
    # parse check only; the real route (SPE or downgrade) is decided when the request is submitted
    route = "spe" if request.requested_as.strip().lower() == "health data access application" else "statistics"
    try:
        query.interpret(draft, route, conn)
        computable = True
    except (mapping.InterpretError, ValueError):
        computable = False
    return Suggestion(decision="ok", articles=verdict.articles, reason=verdict.reason,
                      definitions=definitions, note=answer.get("note", ""), computable=computable,
                      language=lang.language)
