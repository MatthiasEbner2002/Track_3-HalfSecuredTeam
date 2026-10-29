"""Wires the layers together in the order of the architecture diagram:

Request -> decision -> (reject) | (accept -> route -> statistics/SPE spec
-> mapping -> query -> execute -> small-cell rule) -> result
"""

import sqlite3

from app import db
from app.layers import decision, mapping, query, routing, small_cell, spe, statistics
from app.models import DataRequest, PipelineResult

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
        )

    route = routing.choose_route(request, verdict)
    spec = _SPEC_BUILDERS[route](request)
    codes = mapping.map_codes(spec)
    plan = query.generate(spec, codes)
    raw = {q.name: db.execute(conn, q.sql, tuple(q.params)) for q in plan.queries}
    data = small_cell.apply(raw, route)

    return PipelineResult(
        request_id=request.id,
        decision=_DECISIONS[route],
        articles=verdict.articles,
        reason=verdict.reason,
        route=route,
        mapping=codes,
        queries=plan.queries,
        data=data,
    )
