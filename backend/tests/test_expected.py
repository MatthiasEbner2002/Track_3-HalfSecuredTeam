"""Scoreboard against requests/expected.json (metric: correct decisions).

Marked xfail while the layers are stubs: run `pytest -rxX tests/test_expected.py`
to see which requests already pass (XPASS). Remove the xfail marker once the
decision layer is real, so regressions fail the build.
"""

import pytest

from app import pipeline

from conftest import REQUEST_IDS, load_request

# Adding Art. 67 or 71 is allowed by the task description.
OPTIONAL_ARTICLES = {"67", "71"}


@pytest.mark.xfail(reason="decision layer is a stub", strict=False)
@pytest.mark.parametrize("request_id", REQUEST_IDS)
def test_decision_and_articles(conn, expected, request_id):
    result = pipeline.run(load_request(request_id), conn)
    assert result.decision == expected[request_id]["decision"]
    assert set(result.articles) - OPTIONAL_ARTICLES == set(expected[request_id]["articles"])
