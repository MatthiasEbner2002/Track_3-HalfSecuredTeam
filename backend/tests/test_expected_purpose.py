"""Scoreboard against requests/expected.json (metric: correct decisions), purpose module.

The decision pipeline is RAG-only now; this checks the standalone app/purpose
rule floor (EHDS_PURPOSE_RULES_ONLY=1): exactly the requests expected to be
REJECTed must hit a prohibited-purpose rule.
"""

import pytest

from app.purpose import assess
from app.purpose.assess import clear_cache

from conftest import REQUEST_IDS, load_request


@pytest.fixture(autouse=True)
def rules_only(monkeypatch):
    monkeypatch.setenv("EHDS_PURPOSE_RULES_ONLY", "1")
    clear_cache()
    yield
    clear_cache()


@pytest.mark.parametrize("request_id", REQUEST_IDS)
def test_prohibited_matches_expected_reject(expected, request_id):
    result = assess(load_request(request_id))
    assert bool(result.prohibited) == (expected[request_id]["decision"] == "REJECT")
