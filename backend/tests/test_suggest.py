"""Definition suggestions: law check first, then a draft that the parser can read."""

from app import suggest
from app.layers import decision
from app.models import Decision

from conftest import load_request


class FakeLLM:
    def __init__(self, definitions):
        self.definitions, self.calls = definitions, 0

    def complete_json(self, system, user, schema):
        self.calls += 1
        assert "p-" not in user  # only request text, never data
        return {"definitions": self.definitions, "note": "drafted"}


def test_prohibited_purpose_gets_no_draft(conn, monkeypatch):
    monkeypatch.setattr(decision, "decide", lambda req: Decision(accepted=False, articles=["54"], reason="premiums"))
    llm = FakeLLM(["should not be asked"])
    out = suggest.suggest(load_request("R02").model_copy(update={"definitions": []}), conn, llm=llm)
    assert out.decision == "REJECT" and out.articles == ["54"] and out.definitions == [] and llm.calls == 0


def test_draft_is_checked_with_the_parser(conn, monkeypatch):
    monkeypatch.setattr(decision, "decide", lambda req: Decision(accepted=True, articles=["53"], reason="research"))
    r01 = load_request("R01")
    good = FakeLLM(r01.definitions)
    out = suggest.suggest(r01.model_copy(update={"definitions": []}), conn, llm=good)
    assert out.decision == "ok" and out.definitions == r01.definitions and out.computable is True and out.note == "drafted"

    vague = FakeLLM(["Report per planet: the vibe."])
    out = suggest.suggest(r01.model_copy(update={"definitions": []}), conn, llm=vague)
    assert out.decision == "ok" and out.computable is False
