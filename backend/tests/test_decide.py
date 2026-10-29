"""Tests for the rule-based decision engine."""

import json
from pathlib import Path

import pytest

from app.models.schemas import DataRequest
from app.services.decide import decide

# Load all 10 real requests from requests/
_REQUESTS_DIR = Path(__file__).parent.parent.parent / "requests"


def _load_request(rid: str) -> DataRequest:
    with open(_REQUESTS_DIR / f"{rid}.json") as f:
        return DataRequest(**json.load(f))


def _load_expected() -> dict:
    with open(_REQUESTS_DIR / "expected.json") as f:
        return json.load(f)


EXPECTED = _load_expected()


@pytest.mark.parametrize("request_id", ["R01", "R02", "R03", "R04", "R05", "R06", "R07", "R08", "R09", "R10"])
def test_decision_matches_expected(request_id: str):
    """Decision and articles must match expected.json (extra articles are allowed per spec)."""
    req = _load_request(request_id)
    decision = decide(req)
    exp = EXPECTED[request_id]

    assert decision.decision.value == exp["decision"], (
        f"{request_id}: expected {exp['decision']}, got {decision.decision}"
    )

    expected_articles = set(exp["articles"])
    actual_articles = set(decision.articles)
    # Extra articles (67, 71) are acceptable per the task spec
    assert expected_articles.issubset(actual_articles), (
        f"{request_id}: expected articles {expected_articles} not all in {actual_articles}"
    )


def test_reject_insurance():
    """R02: insurance premium pricing → REJECT."""
    req = _load_request("R02")
    d = decide(req)
    assert d.decision.value == "REJECT"
    assert "54" in d.articles


def test_reject_advertising():
    """R04: advertising campaign → REJECT."""
    req = _load_request("R04")
    d = decide(req)
    assert d.decision.value == "REJECT"


def test_reject_employment():
    """R06: employee health decisions → REJECT."""
    req = _load_request("R06")
    d = decide(req)
    assert d.decision.value == "REJECT"


def test_approve_spe():
    """R05: needs patient-level data → APPROVE_SPE."""
    req = _load_request("R05")
    d = decide(req)
    assert d.decision.value == "APPROVE_SPE"
    assert "66" in d.articles
    assert "73" in d.articles


def test_approve_statistical_health_data_request():
    """R01: health data request → APPROVE_STATISTICAL."""
    req = _load_request("R01")
    d = decide(req)
    assert d.decision.value == "APPROVE_STATISTICAL"
    assert "69" in d.articles


def test_approve_statistical_access_application():
    """R09: access application without SPE justification → APPROVE_STATISTICAL."""
    req = _load_request("R09")
    d = decide(req)
    assert d.decision.value == "APPROVE_STATISTICAL"
    assert "66" in d.articles
