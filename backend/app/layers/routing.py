"""Choose between statistics (default) and SPE for an accepted request.

Decision order (challenge README, Regulation (EU) 2025/327 Chapter IV):
- health data request                              -> statistics, Art. 69
- access application that explains why statistics
  are not enough (patient-level data needed)       -> SPE, Art. 66, 68, 73
- any other access application                     -> statistics, Art. 66, 69 (downgrade)

The SPE check has a deterministic floor and a RAG step. Floor: a sentence of
the application that says patient-level data is needed, or that aggregates are
not enough (the wordings below), is the justification itself and routes to the
SPE without asking a model. Otherwise the two points of Art. 66 that set the
legal test are fetched by id and the LLM decides from the application text. Its
quote is verified against the request; an unverifiable quote downgrades to
statistics and flags the result for staff review — statistics never leak records.
"""

import re
from dataclasses import dataclass

from app.layers import decision
from app.layers.decision import LLM
from app.models import DataRequest, Evidence, LawParagraph, Route
from app.rag.store import Retriever

SPE_PROMPT = """You review a health data access application under the European Health Data Space Regulation (EU) 2025/327. The applicant asks for pseudonymised patient-level records. The law below (Art. 66(2) and 66(3)) says when that is allowed: only if the purpose cannot be achieved with anonymised, aggregated statistics.

Decide whether the application itself states such a reason. It is justified when the application says, in its own words, that
- a model or algorithm is trained, fitted, validated or evaluated on individual records or per patient, or
- the analysis needs the order, sequence, timing or follow-up of events within a patient (longitudinal, trajectories, survival, time-to-event), or
- records have to be linked or analysed at record level, or
- aggregated statistics / summary tables / counts are not enough, insufficient or cannot answer the question.
It is NOT justified when the application only asks for records, or when its purpose is a count, prevalence, share, mean, benchmark, capacity plan, surveillance or teaching material that aggregated tables per group could deliver.

Judge only what the text says; do not infer a justification that is not written. If justified, copy word for word the one sentence of the application that states the reason (exactly as written, no paraphrase); otherwise set justified to false and leave the quote empty."""

SPE_SCHEMA = {
    "type": "object",
    "properties": {"quote": {"type": "string"}, "justified": {"type": "boolean"}},
    "required": ["quote", "justified"],
}


@dataclass
class RouteChoice:
    route: Route
    articles: list[str]
    reason: str
    grounding: list[LawParagraph]
    evidence: list[Evidence]
    flags: list[str]
    needs_review: bool = False


@dataclass
class SpeCheck:
    justified: bool
    quote: str
    verified: bool


# Floor for Art. 66(3): "<needs/requires/trains ...> <patient-level/individual/longitudinal ...>" in one
# sentence, or "<aggregates/summary tables/statistics> <are not enough/insufficient/cannot ...>".
_NEED = re.compile(r"\b(requires?|required|needs?|needed|necessary|depends? on|cannot be (?:achieved|done|answered)"
                   r"|not (?:enough|sufficient|possible)|insufficient|only possible|must be trained|train(?:ed|ing|s)?"
                   r"|validat(?:e|ed|ing|es)|fit(?:ted)?)\b", re.I)
_PATIENT_LEVEL = re.compile(r"\b(patient[- ]level|individual[- ]level|record[- ]level|person[- ]level"
                            r"|individual(?: patient)? (?:histories|records|data|trajectories)|longitudinal|per-patient"
                            r"|patient histories|each patient'?s?|sequence of|order of (?:diagnoses|events)|linkage)\b", re.I)
_AGGREGATE = re.compile(r"\b(aggregat\w*|summary tables?|statistics|counts)\b", re.I)
_NOT_ENOUGH = re.compile(r"\b(not (?:enough|sufficient|suffice)|insufficient|cannot|can ?not"
                         r"|do(?:es)? not (?:allow|suffice|capture|work)|would not|lose|hide)\b", re.I)


def spe_justification_sentence(request: DataRequest) -> str | None:
    """The sentence of purpose or question that justifies patient-level data by itself, if any."""
    for sentence in re.split(r"(?<=[.!?])\s+", f"{request.purpose} {request.question}"):
        if (_NEED.search(sentence) and _PATIENT_LEVEL.search(sentence)) or (
            _AGGREGATE.search(sentence) and _NOT_ENOUGH.search(sentence)
        ):
            return sentence.strip()
    return None


def is_health_data_request(request: DataRequest) -> bool:
    return " ".join(request.requested_as.lower().split()) == "health data request"


def _grounding(retriever: Retriever, chunk_ids: list[str]) -> list[LawParagraph]:
    return [p for p in (retriever.get(chunk_id) for chunk_id in chunk_ids) if p is not None]


def check_spe(request: DataRequest, retriever: Retriever, llm: LLM) -> SpeCheck:
    """Whether the application justifies pseudonymised patient-level data (Art. 66(3))."""
    law66_2 = retriever.get("Art. 66(2)")
    law66_3 = retriever.get("Art. 66(3)")
    user = (
        f"LAW:\n[Art. 66(2)] {law66_2.text if law66_2 else ''}\n"
        f"[Art. 66(3)] {law66_3.text if law66_3 else ''}\n\n"
        f"APPLICATION\nPurpose: {request.purpose}\nQuestion: {request.question}\nDefinitions:\n"
        + "\n".join(f"- {d}" for d in request.definitions)
    )
    answer = llm.complete_json(SPE_PROMPT, user, SPE_SCHEMA)
    justified = bool(answer.get("justified"))
    quote = str(answer.get("quote") or "").strip()
    words = decision.normalise(quote)
    request_text = decision.normalise(
        request.purpose + " " + request.question + " " + " ".join(request.definitions)
    )
    verified = justified and len(words.split()) >= 3 and words in request_text
    return SpeCheck(justified, quote, verified)


def _spe_choice(retriever: Retriever, quote: str, source: str) -> RouteChoice:
    return RouteChoice(
        route="spe",
        articles=["66", "68", "73"],
        reason=f"The applicant explains why statistics are not enough: “{quote}” "
        "Pseudonymised data is therefore justified (Art. 66(3)), and is available only "
        "under a data permit inside a secure processing environment, without download "
        "(Art. 68, 73(1)).",
        grounding=_grounding(retriever, ["Art. 66(3)", "Art. 68(1)(c)", "Art. 73(1)"]),
        evidence=[Evidence(point="Art. 66(3)", label="patient-level data is justified", phrase=quote, source=source)],
        flags=[],
    )


def _choose_uncached(request: DataRequest, retriever: Retriever, llm: LLM) -> RouteChoice:
    if is_health_data_request(request):
        return RouteChoice(
            route="statistics",
            articles=["69"],
            reason="This is a health data request, so the answer is given only as "
            "anonymised statistics (Art. 69(1)).",
            grounding=_grounding(retriever, ["Art. 69(1)"]),
            evidence=[],
            flags=[],
        )

    if sentence := spe_justification_sentence(request):
        return _spe_choice(retriever, sentence, source="rule")
    check = check_spe(request, retriever, llm)
    if check.justified and check.verified:
        return _spe_choice(retriever, check.quote, source="llm")

    choice = RouteChoice(
        route="statistics",
        articles=["66", "69"],
        reason="The application does not show that the purpose cannot be achieved with "
        "anonymised data, so pseudonymised records are not justified (Art. 66(2)). "
        "Approved as anonymised statistics instead (Art. 69).",
        grounding=_grounding(retriever, ["Art. 66(2)", "Art. 69(1)"]),
        evidence=[],
        flags=[],
    )
    if check.justified:  # claimed a justification but the quote could not be verified
        choice.needs_review = True
        choice.flags.append("spe_quote_unverified")
    return choice


def choose(
    request: DataRequest, retriever: Retriever | None = None, llm: LLM | None = None
) -> RouteChoice:
    """Route an accepted request; calls the model afresh every time (no caching)."""
    return _choose_uncached(
        request,
        retriever or decision._default_retriever(),
        llm or decision._default_llm(),
    )
