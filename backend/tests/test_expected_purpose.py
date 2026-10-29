"""Scoreboard against requests/expected.json (metric: correct decisions), purpose engine.

Runs with the NLI model when it is installed, otherwise with the rule floor
(EHDS_PURPOSE_RULES_ONLY=1 forces that). Both must reach 10 of 10.
"""

import pytest

from app import pipeline
from app.layers import decision

from conftest import REQUEST_IDS, load_request

# Adding Art. 67 or 71 is allowed by the task description.
OPTIONAL_ARTICLES = {"67", "71"}


@pytest.fixture(autouse=True)
def purpose_engine(monkeypatch):
    monkeypatch.setattr(decision, "DECISION_ENGINE", "purpose")


@pytest.mark.parametrize("request_id", REQUEST_IDS)
def test_decision_and_articles(conn, expected, request_id):
    result = pipeline.run(load_request(request_id), conn)
    assert result.decision == expected[request_id]["decision"]
    assert set(result.articles) - OPTIONAL_ARTICLES == set(expected[request_id]["articles"])
