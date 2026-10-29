"""Wires the layers together in the order of the architecture diagram:

Request -> decision -> (reject) | (accept -> route -> statistics/SPE spec
-> mapping -> query -> execute -> small-cell rule) -> result
"""

import sqlite3

from app import db
from app.layers import decision, mapping, query, routing
from app.models import CodeMapping, DataRequest, PipelineResult, QueryPlan
from app.purpose import assess

_DECISIONS = {"statistics": "APPROVE_STATISTICAL", "spe": "APPROVE_SPE"}


def run(request: DataRequest, conn: sqlite3.Connection) -> PipelineResult:
    verdict = decision.decide(request)
    if not verdict.accepted:
        return PipelineResult(
            request_id=request.id,
            decision="REJECT",
            articles=verdict.articles,
            reason=verdict.reason,
            grounding=verdict.grounding,
            needs_review=verdict.needs_review,
            evidence=verdict.evidence,
            flags=verdict.flags,
        )

    route = routing.choose_route(request, verdict)
    route_articles, route_reason = routing.explain(request, route)
    articles = list(dict.fromkeys(verdict.articles + route_articles))
    reason = f"{verdict.reason} {route_reason}".strip()
    flags = list(verdict.flags)
    codes, plan, data = CodeMapping(), QueryPlan(), {}
    try:
        query_spec, source = query.interpret(request, route, conn)
        if source == "llm":
            flags.append("llm_interpreted")  # rules could not read it; Bob's reading, staff should check
        codes = mapping.to_code_mapping(query_spec)
        plan = query.compile_spec(query_spec)
        rows = {q.name: db.execute(conn, q.sql, q.params) for q in plan.queries}
        data = query.answer(query_spec, plan, rows, conn)  # small-cell rule applied here
    except mapping.InterpretError as e:
        flags.append(f"not_interpreted: {e}")

    return PipelineResult(
        request_id=request.id,
        decision=_DECISIONS[route],
        articles=articles,
        reason=reason,
        grounding=verdict.grounding,
        needs_review=verdict.needs_review,
        evidence=verdict.evidence + ([spe_why] if (spe_why := assess(request).spe_justification) and route == "spe" else []),
        flags=flags,
        route=route,
        mapping=codes,
        queries=plan.queries,
        data=data,
    )
