"""Decision layer against expected.json and the held-out set, using the real local LLM.

Skipped unless Ollama runs and data/law.db is built. For a readable report run
    python -m app.eval_decisions --runs 3
"""

import pytest

from app.eval_decisions import cases
from app.layers import decision

pytestmark = pytest.mark.real_llm

CASES = cases()
IDS = [request.id for request, _, _ in CASES]


@pytest.mark.parametrize(("request_", "accept", "point"), CASES, ids=IDS)
def test_accept_or_reject(request_, accept, point):
    result = decision.decide(request_)
    assert result.accepted == accept
    assert result.articles[0] == ("53" if accept else "54")


@pytest.mark.parametrize(("request_", "accept", "point"), CASES[:10], ids=IDS[:10])
def test_consistent_across_three_runs(request_, accept, point):
    assert len({decision.decide(request_).model_dump_json() for _ in range(3)}) == 1
