"""Integration tests: run all 10 requests through the full pipeline and compare
to requests/expected.json.

These tests load the real data, build the SQLite DB and check that:
  - decisions and articles are correct
  - every expected numeric value appears somewhere in the answer
  - no raw patient-level data leaks (counts ≥ 10 or "<10", no raw patient IDs)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import app

_REQUESTS_DIR = Path(__file__).parent.parent.parent / "requests"


def _load_request(rid: str) -> dict:
    with open(_REQUESTS_DIR / f"{rid}.json") as f:
        return json.load(f)


def _load_expected() -> dict:
    with open(_REQUESTS_DIR / "expected.json") as f:
        return json.load(f)


EXPECTED = _load_expected()


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def _flatten_values(obj: Any) -> list[Any]:
    """Recursively collect all leaf values from a nested dict/list."""
    if isinstance(obj, dict):
        result = []
        for v in obj.values():
            result.extend(_flatten_values(v))
        return result
    elif isinstance(obj, list):
        result = []
        for item in obj:
            result.extend(_flatten_values(item))
        return result
    else:
        return [obj]


def _submit(client: TestClient, rid: str) -> dict:
    body = _load_request(rid)
    resp = client.post("/api/requests", json=body)
    assert resp.status_code == 200, f"{rid}: HTTP {resp.status_code}: {resp.text}"
    return resp.json()


# ---------------------------------------------------------------------------
# Decision and article tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "rid",
    ["R01", "R02", "R03", "R04", "R05", "R06", "R07", "R08", "R09", "R10"],
)
def test_decision(client: TestClient, rid: str):
    result = _submit(client, rid)
    exp = EXPECTED[rid]
    assert result["decision"]["decision"] == exp["decision"], (
        f"{rid}: decision mismatch"
    )
    expected_arts = set(exp["articles"])
    actual_arts = set(result["decision"]["articles"])
    assert expected_arts.issubset(actual_arts), (
        f"{rid}: missing articles {expected_arts - actual_arts}"
    )


# ---------------------------------------------------------------------------
# Answer / numbers tests (approved requests only)
# ---------------------------------------------------------------------------


def _assert_answer_contains(rid: str, actual_answer: dict, expected_answer: Any):
    """Check that every expected value from expected.json appears somewhere in actual."""
    if expected_answer is None:
        assert actual_answer is None or actual_answer == {}, f"{rid}: expected no answer"
        return

    expected_flat = _flatten_values(expected_answer)
    actual_flat = _flatten_values(actual_answer)

    for val in expected_flat:
        assert val in actual_flat, (
            f"{rid}: expected value {val!r} not found in answer.\n"
            f"  Expected: {json.dumps(expected_answer, indent=2)}\n"
            f"  Actual:   {json.dumps(actual_answer, indent=2)}"
        )


@pytest.mark.parametrize("rid", ["R01", "R03", "R07", "R08", "R09"])
def test_statistical_answer(client: TestClient, rid: str):
    """Statistical answers must contain every expected value."""
    result = _submit(client, rid)
    exp = EXPECTED[rid]
    assert result["answer"] is not None, f"{rid}: expected non-null answer"
    _assert_answer_contains(rid, result["answer"], exp["answer"])


def test_r05_spe_cohort_size(client: TestClient):
    """R05 SPE: cohort_size must match expected."""
    result = _submit(client, "R05")
    assert result["answer"] is not None
    expected_size = EXPECTED["R05"]["answer"]["cohort_size"]
    assert result["answer"]["cohort_size"] == expected_size, (
        f"R05: cohort_size {result['answer']['cohort_size']} != {expected_size}"
    )


# ---------------------------------------------------------------------------
# Privacy / disclosure tests
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("rid", ["R01", "R03", "R07", "R08", "R09"])
def test_no_small_cell_leak(client: TestClient, rid: str):
    """No statistical answer should contain a raw count that is > 0 but < 10."""
    result = _submit(client, rid)
    if result["answer"] is None:
        return
    flat = _flatten_values(result["answer"])
    for val in flat:
        if isinstance(val, int):
            assert val == 0 or val >= 10, (
                f"{rid}: raw count {val} violates small-cell rule"
            )


@pytest.mark.parametrize("rid", ["R02", "R04", "R06", "R10"])
def test_rejected_has_no_answer(client: TestClient, rid: str):
    """Rejected requests must have answer = null."""
    result = _submit(client, rid)
    assert result["decision"]["decision"] == "REJECT"
    assert result["answer"] is None, f"{rid}: rejected request leaked answer data"


def test_spe_download_blocked(client: TestClient):
    """SPE download endpoint must return 403."""
    # First ensure R05 is processed
    _submit(client, "R05")
    resp = client.get("/api/spe/R05/download")
    assert resp.status_code == 403
    assert "73" in resp.json()["detail"]


def test_spe_describe(client: TestClient):
    """SPE describe endpoint returns patient count and record types."""
    _submit(client, "R05")
    resp = client.get("/api/spe/R05")
    assert resp.status_code == 200
    data = resp.json()
    assert "patient_count" in data
    assert data["patient_count"] > 0
    assert data["download_blocked"] is True


# ---------------------------------------------------------------------------
# Persistence / GET endpoint tests
# ---------------------------------------------------------------------------


def test_get_request(client: TestClient):
    """Stored result is retrievable via GET /api/requests/{id}."""
    _submit(client, "R01")
    resp = client.get("/api/requests/R01")
    assert resp.status_code == 200
    assert resp.json()["decision"]["decision"] == "APPROVE_STATISTICAL"


def test_list_requests(client: TestClient):
    """GET /api/requests returns all submitted requests."""
    for rid in ["R01", "R02"]:
        _submit(client, rid)
    resp = client.get("/api/requests")
    assert resp.status_code == 200
    ids = [r["request_id"] for r in resp.json()]
    assert "R01" in ids
    assert "R02" in ids
