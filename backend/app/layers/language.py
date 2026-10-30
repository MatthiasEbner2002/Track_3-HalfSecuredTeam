"""Step 0: bring a request written in any official EU language into English.

Everything after this step (Art. 54/53 decision, the Art. 66 floor and quote
checks, the regex rules, the suggest prompt) reads English. One OpenRouter
call detects the language and translates organisation, purpose, question and
definitions; name, country and the two enum fields are left alone. An English
request is passed through verbatim (the model's rewording is discarded), so
English requests behave exactly as before.

Codes, numbers, dates and the region/hospital names must survive the
translation; that is checked in code. A miss gets one retry with the error,
then the translation is used but flagged ``translation_unverified`` so staff
look at it. Only request text reaches the model, never patient data.
"""

import re
import sqlite3
from dataclasses import dataclass, field
from functools import cache

from app.layers.decision import LLM
from app.models import DataRequest
from app.rag.openrouter import OpenRouterLLM

# ISO 639-1 codes of the 24 official EU languages
EU_LANGUAGES = {
    "bg": "Bulgarian", "cs": "Czech", "da": "Danish", "de": "German", "el": "Greek", "en": "English",
    "es": "Spanish", "et": "Estonian", "fi": "Finnish", "fr": "French", "ga": "Irish", "hr": "Croatian",
    "hu": "Hungarian", "it": "Italian", "lt": "Lithuanian", "lv": "Latvian", "mt": "Maltese", "nl": "Dutch",
    "pl": "Polish", "pt": "Portuguese", "ro": "Romanian", "sk": "Slovak", "sl": "Slovenian", "sv": "Swedish",
}

TRANSLATED_FIELDS = ("organisation", "purpose", "question", "definitions")

PROMPT = """You prepare requests for a Health Data Access Body (EHDS, Regulation (EU) 2025/327). Requests may arrive in any
official EU language; the access body works in English.

1. "language": the ISO 639-1 code of the language the purpose and question are written in.
2. Translate organisation, purpose, question and every definition into plain English, literally and completely.
   Do not shorten, add, merge, split, soften or reorder anything: the translation is judged by the law and must say
   exactly what the applicant said. Keep the number and order of definitions.
   Keep unchanged: ICD-10, ATC and LOINC codes, numbers, dates, percentages, comparison signs, person and
   organisation names, and these data names: regions {regions}; hospitals {hospitals}.
   Use the wording of these accepted English requests where it fits (e.g. "any condition with an ICD-10 code
   starting with E11", "aged 18 or older on 31 December 2025", "Report per region: ...", "Cohort = ..."):
{examples}
3. If the text is already English, copy it unchanged.

Answer with one JSON object only."""

SCHEMA = {
    "type": "object",
    "properties": {
        "language": {"type": "string", "enum": sorted(EU_LANGUAGES)},
        "organisation": {"type": "string"},
        "purpose": {"type": "string"},
        "question": {"type": "string"},
        "definitions": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["language", "organisation", "purpose", "question", "definitions"],
    "additionalProperties": False,
}

# Tokens that must survive a translation: ICD-10 / ATC codes, LOINCs, every number. Numeric dates
# (31.12.2025, 2025-12-31) are reduced to day and year first, since English writes the month as a word.
_TOKENS = re.compile(r"\b(?:[A-Z]\d{2}(?:\.\d+)?[A-Z]{0,2}\d{0,2}|\d{4,5}-\d|\d+)\b")
_DATE = re.compile(r"\b(?:(\d{1,2})\.(\d{1,2})\.(\d{4})|(\d{4})-(\d{2})-(\d{2}))\b")
EXAMPLES = [
    "Adult = aged 18 or older on 31 December 2025.",
    "Type 2 diabetes = any condition with an ICD-10 code starting with E11.",
    "Prevalence = adults with type 2 diabetes / all adults in the region × 100, one decimal.",
    "Report per region: adults, adults with type 2 diabetes and the prevalence.",
    "Oral anticoagulant = ATC code starting with B01AA or B01AF.",
    "Use the latest BMI (LOINC 39156-5) per patient; patients without a BMI are left out.",
    "Inpatient stay = encounter with type 'inpatient'.",
    "Cohort = adults with type 2 diabetes who have at least one HbA1c value (LOINC 4548-4).",
    "Report the size of the cohort you would make available.",
]


@dataclass
class Translation:
    """What the applicant wrote and what the access body judged."""

    language: str = "en"
    original: dict | None = None  # the four fields as submitted; None when already English
    verified: bool = True  # codes, numbers and dates survived the translation
    attempts: int = 0
    flags: list[str] = field(default_factory=list)

    @property
    def translated(self) -> bool:
        return self.language != "en"


@cache
def _default_llm() -> LLM:
    return OpenRouterLLM()


def _names(conn: sqlite3.Connection | None) -> dict[str, str]:
    if conn is None:
        return {"regions": "as in the data", "hospitals": "as in the data"}
    regions = [r for (r,) in conn.execute("SELECT DISTINCT region FROM patients ORDER BY region")]
    hospitals = [h for (h,) in conn.execute("SELECT DISTINCT data_holder FROM patients ORDER BY data_holder")]
    return {"regions": ", ".join(regions), "hospitals": ", ".join(hospitals)}


def _text(request: DataRequest) -> dict:
    return {"organisation": request.applicant.organisation, "purpose": request.purpose,
            "question": request.question, "definitions": list(request.definitions)}


def tokens(text: str) -> set[str]:
    text = _DATE.sub(lambda m: f"{int(m[1] or m[6])} {m[3] or m[4]}", text)
    return set(_TOKENS.findall(text))


def check(original: dict, answer: dict) -> list[str]:
    """Problems with a translation, as messages for the retry. Empty = fine."""
    problems = []
    if len(answer.get("definitions", [])) != len(original["definitions"]):
        problems.append(f"expected {len(original['definitions'])} definitions, got {len(answer.get('definitions', []))}")
    for name in TRANSLATED_FIELDS:
        src = " ".join(original[name]) if name == "definitions" else original[name]
        dst = " ".join(answer.get(name, [])) if name == "definitions" else str(answer.get(name, ""))
        if src.strip() and not dst.strip():
            problems.append(f"{name} is empty")
        if lost := sorted(tokens(src) - tokens(dst)):
            problems.append(f"{name}: these must appear unchanged: {', '.join(lost)}")
    return problems


def localise(request: DataRequest, conn: sqlite3.Connection | None = None, llm: LLM | None = None
             ) -> tuple[DataRequest, Translation]:
    """The request in English plus what happened; English in, same object out."""
    llm = llm or _default_llm()
    original = _text(request)
    system = PROMPT.format(examples="\n".join(f"- {e}" for e in EXAMPLES), **_names(conn))
    user = ("Organisation: " + original["organisation"] + "\nPurpose: " + original["purpose"]
            + "\nQuestion: " + original["question"] + "\nDefinitions:\n"
            + "\n".join(f"- {d}" for d in original["definitions"]))
    answer = llm.complete_json(system, user, SCHEMA)
    attempts = 1
    language = answer.get("language", "en")
    if language not in EU_LANGUAGES or language == "en":
        return request, Translation(attempts=attempts)

    problems = check(original, answer)
    if problems:
        retry = user + "\n\nYour previous translation was rejected: " + "; ".join(problems) + ". Translate again."
        answer, attempts = llm.complete_json(system, retry, SCHEMA), 2
        problems = check(original, answer)

    translated = DataRequest.model_validate({
        **request.model_dump(),
        "applicant": {**request.applicant.model_dump(), "organisation": answer["organisation"]},
        "purpose": answer["purpose"],
        "question": answer["question"],
        "definitions": [str(d).strip() for d in answer["definitions"] if str(d).strip()],
    })
    info = Translation(language=language, original=original, verified=not problems, attempts=attempts,
                       flags=[f"translated_from:{language}"] + (["translation_unverified"] if problems else []))
    return translated, info
