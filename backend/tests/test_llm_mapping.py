"""Stage 2 with Bob replaced by a fake: validation, retry, refusal, cache and fallback order."""

import json

import pytest

from app.layers import llm_mapping, query
from app.layers.mapping import InterpretError, NoDataError
from app.models import DataRequest

# Wording the rule parser does not read (checked in test_rules_cannot_read_it).
UNREADABLE = DataRequest(
    id="T", applicant={"name": "x", "organisation": "y", "country": "AT"}, requested_as="health data request",
    purpose="research", question="How common is diabetes, by hospital?",
    definitions=["Diabetics: everybody carrying an E10 or E11 code in their problem list.",
                 "Break the result down by hospital and give the number of diabetics."],
)
GOOD = {
    "concepts": [{"name": "diabetics", "kind": "diagnosis", "codes": ["E10", "E11"]}],
    "group_by": ["hospital"],
    "metrics": [{"label": "number of diabetics", "op": "count", "where": [[{"concept": "diabetics"}]]}],
}


@pytest.fixture
def bob(monkeypatch, tmp_path):
    """Queue of answers the fake Bob gives, one per call."""
    answers: list[str] = []
    calls: list[str] = []

    def fake(prompt: str) -> str:
        calls.append(prompt)
        return answers.pop(0)

    monkeypatch.setattr(llm_mapping, "_ask_bob", fake)
    monkeypatch.setattr(llm_mapping, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(llm_mapping, "available", lambda: True)
    return answers, calls


def test_rules_cannot_read_it(conn):
    with pytest.raises(InterpretError):
        query.parse(UNREADABLE, "statistics", conn)


def test_bob_fills_the_spec(conn, bob):
    answers, calls = bob
    answers.append("Here you go:\n" + json.dumps(GOOD))
    spec, source = query.interpret(UNREADABLE, "statistics", conn)
    assert source == "llm" and spec.group_by == ["hospital"]
    assert "could not read this request" in calls[0]
    result = query.extract(UNREADABLE, "statistics", conn)  # second time from the cache
    assert len(calls) == 1
    assert set(result) == {"Klinikum Nord", "Klinikum Süd", "Klinikum West"}


def test_invalid_answer_gets_one_retry(conn, bob):
    answers, calls = bob
    bad = dict(GOOD, metrics=[{"label": "mean", "op": "mean", "measurement": "diabetics"}])
    answers += [json.dumps(bad), json.dumps(GOOD)]
    spec, _ = query.interpret(UNREADABLE, "statistics", conn)
    assert len(calls) == 2 and "rejected" in calls[1]
    assert spec.metrics[0].op == "count"


def test_two_invalid_answers_fail_loudly(conn, bob):
    answers, _ = bob
    answers += ["no json here", "still none"]
    with pytest.raises(InterpretError, match="did not validate"):
        query.interpret(UNREADABLE, "statistics", conn)


def test_bob_may_refuse(conn, bob):
    answers, calls = bob
    answers.append(json.dumps({"error": "per district is not in the data"}))
    with pytest.raises(InterpretError, match="not answerable"):
        query.interpret(UNREADABLE, "statistics", conn)
    assert len(calls) == 1


def test_codes_missing_from_the_data_never_go_to_bob(conn, bob):
    _, calls = bob
    req = UNREADABLE.model_copy(update={"definitions": [
        "Asthma = ICD-10 Q99.", "Report per region: patients with asthma."]})
    with pytest.raises(NoDataError):
        query.interpret(req, "statistics", conn)
    assert calls == []


def test_codes_bob_takes_from_the_request_must_exist_in_the_data(conn, bob):
    answers, _ = bob
    req = UNREADABLE.model_copy(update={"definitions": [
        "Diabetics: everybody carrying a Q99 code in their problem list.",
        "Break the result down by hospital and give the number of diabetics."]})
    answers += [json.dumps(dict(GOOD, concepts=[{"name": "diabetics", "kind": "diagnosis", "codes": ["Q99"]}]))]
    with pytest.raises(NoDataError):
        query.interpret(req, "statistics", conn)


def test_readable_requests_never_go_to_bob(conn, bob, r01):
    _, calls = bob
    _, source = query.interpret(r01, "statistics", conn)
    assert source == "rules" and calls == []


def test_bob_may_not_bring_its_own_codes(conn, bob):
    answers, _ = bob
    req = UNREADABLE.model_copy(update={"definitions": [
        "Diabetics: everybody carrying an E10 or E11 code in their problem list.",
        "Break the result down by hospital: number of diabetics, share on a statin."]})
    statin = {"name": "statin", "kind": "drug", "codes": ["C10AA"]}
    answers.append(json.dumps(dict(GOOD, concepts=GOOD["concepts"] + [statin])))
    with pytest.raises(InterpretError, match="does not give"):
        query.interpret(req, "statistics", conn)
