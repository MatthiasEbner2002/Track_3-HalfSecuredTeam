"""Wires the layers together in the order of the architecture diagram:

Request -> language (any EU language -> English) -> decision -> (reject)
| (accept -> route -> statistics/SPE spec -> mapping -> query -> execute
-> small-cell rule) -> result
"""

import sqlite3

from app import db, spe
from app.config import SPE_FRONTEND_URL
from app.layers import decision, language, mapping, query, routing
from app.models import CodeMapping, DataRequest, PipelineResult, QueryPlan

_DECISIONS = {"statistics": "APPROVE_STATISTICAL", "spe": "APPROVE_SPE"}


def run(request: DataRequest, conn: sqlite3.Connection) -> PipelineResult:
    request, lang = language.localise(request, conn)
    # every layer below reads the English text; the result shows it next to what the applicant wrote
    translated = {"organisation": request.applicant.organisation, "purpose": request.purpose,
                  "question": request.question, "definitions": request.definitions} if lang.translated else None

    verdict = decision.decide(request)
    if not verdict.accepted:
        return PipelineResult(
            request_id=request.id,
            decision="REJECT",
            articles=verdict.articles,
            reason=verdict.reason,
            grounding=verdict.grounding,
            needs_review=verdict.needs_review or not lang.verified,
            evidence=verdict.evidence,
            flags=lang.flags + verdict.flags,
            language=lang.language,
            translated=translated,
        )

    choice = routing.choose(request)
    route = choice.route
    articles = list(dict.fromkeys(verdict.articles + choice.articles))
    reason = f"{verdict.reason} {choice.reason}".strip()
    flags = lang.flags + verdict.flags + choice.flags
    codes, plan, data = CodeMapping(), QueryPlan(), {}
    try:
        query_spec, source = query.interpret(request, route, conn)
        if source == "llm":
            flags.append("llm_interpreted")  # rules could not read it; LLM reading, staff should check
        codes = mapping.to_code_mapping(query_spec)
        plan = query.compile_spec(query_spec)
        rows = {q.name: db.execute(conn, q.sql, q.params) for q in plan.queries}
        data = query.answer(query_spec, plan, rows, conn)  # small-cell rule applied here
        if route == "spe":  # copy the cohort's named records into the permit's own SPE (Art. 73)
            spe_info, key = spe.create(request, conn, [r["patient_id"] for r in rows["cohort"]],
                                       query_spec.record_types, articles, reason)
            # the key is the applicant's only way in; it is not stored anywhere else
            data["spe"] = {"id": request.id, "tables": spe_info["tables"], "access_key": key,
                           "key_expires": spe_info["key_expires"], "url": SPE_FRONTEND_URL}
    except mapping.InterpretError as e:
        flags.append(f"not_interpreted: {e}")

    return PipelineResult(
        request_id=request.id,
        decision=_DECISIONS[route],
        articles=articles,
        reason=reason,
        grounding=verdict.grounding + choice.grounding,
        needs_review=verdict.needs_review or choice.needs_review or not lang.verified,
        evidence=verdict.evidence + choice.evidence,
        flags=flags,
        route=route,
        mapping=codes,
        queries=plan.queries,
        data=data,
        language=lang.language,
        translated=translated,
    )
