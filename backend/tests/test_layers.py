"""Contract tests: each layer returns the type the next layer expects.

These must keep passing when a stub is replaced by a real implementation.
"""

from app.layers import decision, mapping, query, routing, small_cell, spe, statistics
from app.models import CodeMapping, Decision, QueryPlan, ResultSpec


def test_decision_returns_decision(r01):
    assert isinstance(decision.decide(r01), Decision)


def test_routing_returns_known_route(r01):
    assert routing.choose_route(r01, decision.decide(r01)) in ("statistics", "spe")


def test_statistics_spec(r01):
    spec = statistics.build_spec(r01)
    assert isinstance(spec, ResultSpec)
    assert spec.route == "statistics"
    assert spec.request.id == "R01"


def test_spe_spec_names_record_types(r01):
    spec = spe.build_spec(r01)
    assert spec.route == "spe"
    assert spec.record_types


def test_mapping_returns_code_mapping(r01):
    assert isinstance(mapping.map_codes(statistics.build_spec(r01)), CodeMapping)


def test_query_plan_runs_on_db(conn, r01):
    spec = statistics.build_spec(r01)
    plan = query.generate(spec, mapping.map_codes(spec))
    assert isinstance(plan, QueryPlan) and plan.queries
    for q in plan.queries:
        conn.execute(q.sql, q.params)  # valid SQL against the real schema


def test_small_cell_keeps_query_names():
    results = {"a": [{"n": 1}], "b": []}
    assert set(small_cell.apply(results, "statistics")) == {"a", "b"}
