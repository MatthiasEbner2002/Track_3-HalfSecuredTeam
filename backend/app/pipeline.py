"""Wires the layers together in the order of the architecture diagram:

Request -> decision -> (reject) | (accept -> route -> statistics/SPE spec
-> mapping -> query -> execute -> small-cell rule) -> result
"""

import sqlite3

from app import db
from app.layers import decision, mapping, query, routing, small_cell, spe, statistics
from app.models import DataRequest, PipelineResult, QueryPlan
from app.purpose import assess

_SPEC_BUILDERS = {"statistics": statistics.build_spec, "spe": spe.build_spec}
_DECISIONS = {"statistics": "APPROVE_STATISTICAL", "spe": "APPROVE_SPE"}


def run(request: DataRequest, conn: sqlite3.Connection) -> PipelineResult:
    verdict = decision.decide(request)
    if not verdict.accepted:
        return PipelineResult(
            request_id=request.id,
            decision="REJECT",
            articles=verdict.articles,
            reason=verdict.reason,
            evidence=verdict.evidence,
            flags=verdict.flags,
        )

    route = routing.choose_route(request, verdict)
    route_articles, route_reason = routing.explain(request, route)
    articles = list(dict.fromkeys(verdict.articles + route_articles))
    reason = f"{verdict.reason} {route_reason}".strip()
    spec = _SPEC_BUILDERS[route](request)
    codes = mapping.map_codes(spec)
    flags = list(verdict.flags)
    try:
        query_spec, source = query.interpret(request, route, conn)
        plan = query.compile_spec(query_spec)
        if source == "llm":
            flags.append("llm_interpreted")  # rules could not read it; Bob's reading, staff should check
    except mapping.InterpretError as e:
        plan = QueryPlan()
        flags.append(f"not_interpreted: {e}")
    raw = {q.name: db.execute(conn, q.sql, q.params) for q in plan.queries}
    data = small_cell.apply(raw, route)

    return PipelineResult(
        request_id=request.id,
        decision=_DECISIONS[route],
        articles=articles,
        reason=reason,
        evidence=verdict.evidence + ([spe_why] if (spe_why := assess(request).spe_justification) and route == "spe" else []),
        flags=flags,
        route=route,
        mapping=codes,
        queries=plan.queries,
        data=data,
    )
