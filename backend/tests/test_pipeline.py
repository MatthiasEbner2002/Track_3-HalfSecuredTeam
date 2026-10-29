import pytest

from app import pipeline
from app.layers import decision, routing
from app.models import Decision

from conftest import REQUEST_IDS, load_request


def test_all_ten_requests_present():
    assert REQUEST_IDS == [f"R{i:02d}" for i in range(1, 11)]


@pytest.mark.parametrize("request_id", REQUEST_IDS)
def test_every_request_runs_end_to_end(conn, request_id):
    result = pipeline.run(load_request(request_id), conn)
    assert result.request_id == request_id
    assert result.decision in ("REJECT", "APPROVE_STATISTICAL", "APPROVE_SPE")
    if result.decision != "REJECT":
        assert result.data is not None


def test_r01_returns_one_row_per_region(conn, r01):
    result = pipeline.run(r01, conn)
    (regions,) = conn.execute("SELECT COUNT(DISTINCT region) FROM patients").fetchone()
    assert len(result.data) == regions
    assert result.data["Burgenland"] == [532, 33, 6.2]  # small-cell rule and rounding applied


def test_reject_branch_stops_before_data(conn, r01, monkeypatch):
    monkeypatch.setattr(
        decision, "decide", lambda req: Decision(accepted=False, articles=["54"], reason="prohibited")
    )
    result = pipeline.run(r01, conn)
    assert result.decision == "REJECT"
    assert result.articles == ["54"]
    assert result.data is None and result.queries == []


def test_spe_branch(conn, r01, monkeypatch):
    monkeypatch.setattr(routing, "choose_route", lambda req, verdict: "spe")
    result = pipeline.run(r01, conn)
    assert result.decision == "APPROVE_SPE"
    assert result.route == "spe"
