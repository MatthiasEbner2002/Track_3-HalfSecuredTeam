"""Language step: any EU language in, English out; English untouched; codes and numbers must survive."""

from app import pipeline
from app.layers import language

from conftest import load_request
from fakes import FakeLLM

R11_ENGLISH = {
    "language": "de",
    "organisation": "Division of Nephrology, Medical University of Graz (university research group)",
    "purpose": "We study how common advanced impairment of kidney function is among adults with chronic kidney "
               "disease, by sex, to plan an early-detection programme of the health insurance funds.",
    "question": "How many adults with chronic kidney disease have a latest eGFR value below 45, by sex?",
    "definitions": [
        "Adult = aged 18 or older on 31 December 2025.",
        "Chronic kidney disease = any condition with an ICD-10 code starting with N18.",
        "Reduced kidney function = latest eGFR (LOINC 62238-1) below 45.",
        "Report per sex: adults with chronic kidney disease, how many of them have reduced kidney function and "
        "that share in %, one decimal.",
    ],
}


def test_english_request_is_passed_through_verbatim():
    r01 = load_request("R01")
    llm = FakeLLM(translation={"language": "en", "organisation": "reworded", "purpose": "reworded",
                               "question": "reworded", "definitions": ["reworded"]})
    out, info = language.localise(r01, llm=llm)
    assert out is r01 and info.language == "en" and not info.translated and info.flags == []
    assert len(llm.calls) == 1 and "p-" not in llm.calls[0][1]  # request text only, never data


def test_german_request_becomes_english(conn):
    r11 = load_request("R11")
    out, info = language.localise(r11, conn, llm=FakeLLM(translation=R11_ENGLISH))
    assert info.language == "de" and info.verified and info.flags == ["translated_from:de"]
    assert out.purpose == R11_ENGLISH["purpose"] and out.definitions == R11_ENGLISH["definitions"]
    assert out.applicant.name == r11.applicant.name and out.applicant.country == "AT"
    assert out.requested_as == r11.requested_as and out.id == "R11"
    assert info.original["purpose"] == r11.purpose


def test_lost_code_is_retried_then_flagged():
    r11 = load_request("R11")
    bad = {**R11_ENGLISH, "definitions": [d.replace("N18", "N17") for d in R11_ENGLISH["definitions"]]}
    llm = FakeLLM(translation=bad)
    out, info = language.localise(r11, llm=llm)
    assert len(llm.calls) == 2 and "N18" in llm.calls[1][1]  # retry names what went missing
    assert not info.verified and "translation_unverified" in info.flags and info.attempts == 2


def test_dropped_definition_is_a_problem():
    problems = language.check({"organisation": "x", "purpose": "p 2025", "question": "q", "definitions": ["a", "b"]},
                              {"organisation": "x", "purpose": "p 2025", "question": "q", "definitions": ["a"]})
    assert problems == ["expected 2 definitions, got 1"]


def test_numeric_dates_compare_by_day_and_year():
    assert language.tokens("am 31.12.2025, 8,0 %") == language.tokens("on 31 December 2025, 8.0 %")
    assert language.tokens("2025-12-01 E11.9 B01AA 4548-4") == {"1", "2025", "E11.9", "B01AA", "4548-4"}


def test_unknown_language_code_counts_as_english():
    r01 = load_request("R01")
    out, info = language.localise(r01, llm=FakeLLM(translation={**R11_ENGLISH, "language": "xx"}))
    assert out is r01 and info.language == "en"


def test_pipeline_answers_the_german_request_with_the_rules(conn, monkeypatch):
    monkeypatch.setattr(language, "_default_llm", lambda: FakeLLM(translation=R11_ENGLISH))
    result = pipeline.run(load_request("R11"), conn)
    assert result.decision == "APPROVE_STATISTICAL" and result.language == "de"
    assert result.translated["question"] == R11_ENGLISH["question"]
    assert "translated_from:de" in result.flags and "llm_interpreted" not in result.flags
    assert result.data["female"]["adults_with_chronic_kidney_disease"] == 219
    assert result.data["male"]["that_share_in_pct"] == 48.7
    assert not result.needs_review


def test_unverified_translation_needs_review(conn, monkeypatch):
    bad = {**R11_ENGLISH, "question": "How many adults have a latest eGFR below forty-five, by sex?"}
    monkeypatch.setattr(language, "_default_llm", lambda: FakeLLM(translation=bad))
    result = pipeline.run(load_request("R11"), conn)
    assert result.needs_review and "translation_unverified" in result.flags
