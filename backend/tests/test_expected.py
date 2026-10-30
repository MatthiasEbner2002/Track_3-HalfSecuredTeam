"""RAG decision engine against expected.json and the held-out set, using the real local LLM.

Skipped unless Ollama runs and data/law.db is built. For a readable report run
    python -m app.eval_decisions --runs 3
The purpose engine has its own scoreboard in test_expected_purpose.py.
"""

import pytest

from app.eval_decisions import cases
from app.layers import decision, routing
from app.rag.openrouter import OpenRouterLLM

from conftest import REQUEST_IDS, load_request

pytestmark = pytest.mark.real_llm

# Adding Art. 67 or 71 is allowed by the task description.
OPTIONAL_ARTICLES = {"67", "71"}
_ROUTES = {"statistics": "APPROVE_STATISTICAL", "spe": "APPROVE_SPE"}

CASES = cases()
IDS = [request.id for request, _, _ in CASES]


@pytest.mark.parametrize(("request_", "accept", "point"), CASES, ids=IDS)
def test_accept_or_reject(request_, accept, point):
    result = decision.decide_rag(request_)
    assert result.accepted == accept
    assert result.articles[0] == ("53" if accept else "54")


@pytest.mark.parametrize(("request_", "accept", "point"), CASES[:10], ids=IDS[:10])
def test_consistent_across_three_runs(request_, accept, point):
    assert len({decision.decide_rag(request_).model_dump_json() for _ in range(3)}) == 1


@pytest.mark.parametrize("request_id", REQUEST_IDS)
def test_decision_and_articles(expected, request_id):
    """decide_rag + routing.choose reproduce the expected decision and articles."""
    request_ = load_request(request_id)
    retriever, llm = decision._default_retriever(), OpenRouterLLM(cache_dir=None)
    verdict = decision.decide_rag(request_, retriever, llm)
    if not verdict.accepted:
        final, articles = "REJECT", verdict.articles
    else:
        choice = routing.choose(request_, retriever, llm)
        final = _ROUTES[choice.route]
        articles = list(dict.fromkeys(verdict.articles + choice.articles))
    assert final == expected[request_id]["decision"]
    assert set(articles) - OPTIONAL_ARTICLES == set(expected[request_id]["articles"])
