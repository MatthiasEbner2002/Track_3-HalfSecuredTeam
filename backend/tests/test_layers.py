"""Contract tests: each layer returns the type the next layer expects."""

from app.layers import decision, mapping, query, routing, small_cell
from app.models import CodeMapping, Decision, QueryPlan, QuerySpec


def test_decision_returns_decision(r01):
    assert isinstance(decision.decide(r01), Decision)


def test_routing_returns_known_route(r01):
    assert routing.choose(r01).route in ("statistics", "spe")


def test_interpret_returns_query_spec_and_source(conn, r01):
    spec, source = query.interpret(r01, "statistics", conn)
    assert isinstance(spec, QuerySpec) and source == "rules"


def test_mapping_returns_code_mapping(conn, r01):
    spec, _ = query.interpret(r01, "statistics", conn)
    assert isinstance(mapping.to_code_mapping(spec), CodeMapping)


def test_query_plan_runs_on_db(conn, r01):
    spec, _ = query.interpret(r01, "statistics", conn)
    plan = query.compile_spec(spec)
    assert isinstance(plan, QueryPlan) and plan.queries
    for q in plan.queries:
        conn.execute(q.sql, q.params)  # valid SQL against the real schema


def test_small_cell_rule():
    assert small_cell.count(9) == "<10" and small_cell.count(10) == 10
    assert small_cell.derived(12.3, 9) == "suppressed" and small_cell.derived(12.3, 10) == 12.3
