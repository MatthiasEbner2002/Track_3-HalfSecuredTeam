"""Decision layer on fakes: rules, grounding and verification, no Ollama needed."""

from app.layers import decision
from conftest import load_request
from fakes import FakeLLM


def test_retrieval_filters_by_kind_and_ranks(fake_retriever):
    hits = fake_retriever.search("advertising marketing", k=1, where=lambda c: c.kind == "point")
    assert [c.id for c in hits] == ["Art. 54(c)"]


def test_extract_sees_purpose_but_not_question():
    request = load_request("R02")
    llm = FakeLLM()
    decision.extract(request, llm)
    _, user, _ = llm.calls[0]
    assert request.purpose in user and request.applicant.organisation in user
    assert request.question not in user


def test_match_offers_permitted_and_prohibited_points_together(fake_retriever):
    llm = FakeLLM()
    decision.decide(load_request("R01"), fake_retriever, llm)
    _, user, schema = llm.calls[-1]
    assert set(schema["properties"]["rule_id"]["enum"]) == {
        "Art. 53(1)(a)", "Art. 53(1)(e)", "Art. 54(b)", "Art. 54(c)"
    }
    assert "PROHIBITED USES" in user and "PERMITTED PURPOSES" in user


def test_prohibited_point_rejects_with_quote(fake_retriever):
    llm = FakeLLM("Art. 54(b)", "insurance premiums")
    result = decision.decide(load_request("R02"), fake_retriever, llm)
    assert not result.accepted
    assert result.articles == ["54"]
    assert [p.id for p in result.grounding] == ["Art. 54(b)"]
    assert "“placeholder decisions on insurance premiums or job offers”" in result.reason
    assert not result.needs_review


def test_permitted_point_accepts(fake_retriever):
    result = decision.decide(load_request("R01"), fake_retriever, FakeLLM())
    assert result.accepted
    assert result.articles == ["53"]
    assert result.reason.startswith("Accepted: permitted purpose under Art. 53(1)(e).")


def test_access_application_gets_base_article_only(fake_retriever):
    """Art. 66 depends on the route (statistics downgrade or SPE), so routing adds it."""
    result = decision.decide(load_request("R09"), fake_retriever, FakeLLM())
    assert result.articles == ["53"]


def test_words_not_in_the_law_need_review(fake_retriever):
    llm = FakeLLM("Art. 54(c)", "selling customer lists")
    result = decision.decide(load_request("R04"), fake_retriever, llm)
    assert not result.accepted
    assert result.needs_review


def test_point_text_strips_marker_and_list_glue():
    point = decision.LawParagraph(id="x", article="54", text="(b) taking decisions; or")
    assert decision.point_text(point) == "taking decisions"
