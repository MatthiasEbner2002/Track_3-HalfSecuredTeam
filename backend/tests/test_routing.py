"""Routing layer on fakes: health data request, SPE check, downgrade, caching."""

from fastapi.testclient import TestClient

from app import pipeline
from app.layers import decision, routing
from app.main import app
from app.rag.ollama import OllamaUnavailable
from app.rag.openrouter import OpenRouterUnavailable

from conftest import load_request
from fakes import FakeLLM

SPE_QUOTE = "We will only export model weights and performance metrics."  # a sentence of R05's question


def spe_calls(llm: FakeLLM) -> int:
    return sum(1 for _, _, schema in llm.calls if "justified" in schema["properties"])


def test_health_data_request_goes_to_statistics_without_llm():
    llm = FakeLLM()
    choice = routing.choose(load_request("R01"), llm=llm)
    assert choice.route == "statistics"
    assert choice.articles == ["69"]
    assert [p.id for p in choice.grounding] == ["Art. 69(1)"]
    assert spe_calls(llm) == 0


def test_requested_as_is_normalised():
    request = load_request("R01").model_copy(update={"requested_as": "Health Data  Request"})
    llm = FakeLLM()
    choice = routing.choose(request, llm=llm)
    assert choice.route == "statistics" and choice.articles == ["69"]
    assert spe_calls(llm) == 0


def _without_floor(request):
    """R05 with a purpose the rule floor does not recognise, so the LLM has to judge."""
    return request.model_copy(update={"purpose": "We build a prediction model for GPs."})


def test_rule_floor_routes_to_spe_without_the_model():
    llm = FakeLLM()
    choice = routing.choose(load_request("R05"), llm=llm)  # "Training needs individual-level histories; aggregates are not enough."
    assert choice.route == "spe" and choice.articles == ["66", "68", "73"]
    assert choice.evidence[0].source == "rule" and "aggregates are not enough" in choice.evidence[0].phrase
    assert spe_calls(llm) == 0


def test_rule_floor_matches_the_known_wordings_only():
    yes = ["Scientific research that requires patient-level longitudinal records.",
           "Students train prediction models on pseudonymised patient-level data.",
           "Summary tables are insufficient because the algorithm scores each patient.",
           "Aggregates are not sufficient: the prediction depends on each patient's course."]
    no = ["We evaluate the regional screening programme and need prevalence figures by region.",
          "Scientific research on regional differences in the quality of care, published in a journal.",
          "Health technology assessment: we estimate the number of eligible patients."]
    base = load_request("R09")
    assert all(routing.spe_justification_sentence(base.model_copy(update={"purpose": p})) for p in yes)
    assert not any(routing.spe_justification_sentence(base.model_copy(update={"purpose": p})) for p in no)


def test_justified_spe_routes_to_spe():
    choice = routing.choose(_without_floor(load_request("R05")), llm=FakeLLM(spe_quote=SPE_QUOTE))
    assert choice.route == "spe"
    assert choice.articles == ["66", "68", "73"]
    assert choice.evidence[0].phrase == SPE_QUOTE
    assert "Art. 66(3)" in {p.id for p in choice.grounding}


def test_unverified_spe_quote_downgrades_to_statistics():
    llm = FakeLLM(spe_quote="a sentence that is not in the application at all")
    choice = routing.choose(_without_floor(load_request("R05")), llm=llm)
    assert choice.route == "statistics"
    assert choice.articles == ["66", "69"]
    assert choice.needs_review
    assert "spe_quote_unverified" in choice.flags


def test_unjustified_access_application_gets_statistics():
    choice = routing.choose(load_request("R09"), llm=FakeLLM())
    assert choice.route == "statistics"
    assert choice.articles == ["66", "69"]
    assert not choice.needs_review


def test_choose_calls_the_model_every_time(monkeypatch):
    llm = FakeLLM()
    monkeypatch.setattr(decision, "_default_llm", lambda: llm)
    routing.choose(_without_floor(load_request("R05")))
    routing.choose(_without_floor(load_request("R05")))
    assert spe_calls(llm) == 2  # no answer caching


def test_pipeline_approves_spe_when_justified(conn, monkeypatch):
    monkeypatch.setattr(decision, "_default_llm", lambda: FakeLLM(spe_quote=SPE_QUOTE))
    result = pipeline.run(load_request("R05"), conn)
    assert result.decision == "APPROVE_SPE"
    assert result.articles == ["53", "66", "68", "73"]


def test_pipeline_approves_statistics_when_not_justified(conn):
    result = pipeline.run(load_request("R09"), conn)
    assert result.decision == "APPROVE_STATISTICAL"
    assert result.articles == ["53", "66", "69"]


def test_api_returns_503_when_ollama_is_down(monkeypatch):
    def boom():
        raise OllamaUnavailable("Ollama is not reachable at http://localhost:11434")

    monkeypatch.setattr(decision, "_default_llm", boom)
    response = TestClient(app).post("/api/requests", json=load_request("R01").model_dump())
    assert response.status_code == 503
    assert "not reachable" in response.json()["detail"]


def test_api_returns_503_when_openrouter_is_down(monkeypatch):
    def boom():
        raise OpenRouterUnavailable("OpenRouter rejected the API key (HTTP 401)")

    monkeypatch.setattr(decision, "_default_llm", boom)
    response = TestClient(app).post("/api/requests", json=load_request("R01").model_dump())
    assert response.status_code == 503
    assert "rejected the API key" in response.json()["detail"]
