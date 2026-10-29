"""Decision system (grounded RAG): accept or reject a request and cite the law.

Three small steps instead of one big question:

1. Extract (LLM): who is asking and what they will do with the results (the
   end use, as opposed to the analysis itself), read
   from the organisation and purpose only. The question is left out on purpose:
   it is always a harmless statistic, the intent is in the purpose.
2. Retrieve: the extracted end use is the search query. It picks the closest
   points of Art. 53(1) (permitted purposes) and of Art. 54 (prohibited uses).
3. Match (LLM, extractive): which one of the retrieved points, permitted or
   prohibited, best covers the intended use, and which words of it do. Both
   kinds compete in one choice, so the model never has to say "none" to a
   list of prohibitions. The words must appear in the point's text; if they
   do not, the decision is flagged for staff review.

The decision follows from the chosen point (Art. 54 rejects, Art. 53 accepts).
The reason is assembled in code from the point's verbatim text, so it cannot
contradict the decision.

Articles returned here are the base set. The routing layer adds the ones that
depend on the route (Art. 69 for statistics, Art. 68 and 73 for an SPE).
"""

import re
from dataclasses import dataclass
from functools import cache
from typing import Protocol

from app.config import RETRIEVAL_TOP_K
from app.models import DataRequest, Decision, LawParagraph
from app.rag.ollama import OllamaEmbedder, OllamaLLM
from app.rag.store import Retriever

ACCESS_APPLICATION = "health data access application"
PERMITTED = "Art. 53(1)"
PROHIBITED = "Art. 54"

EXTRACT_PROMPT = """You read applications for health data under the European Health Data Space. \
Describe the facts only; do not judge whether the use is allowed.
- "actor": who is applying, e.g. "the pricing department of an insurance company".
- "analysis": what the actor will compute from the data, e.g. "count patients with diabetes by region".
- "end_use": what the actor will do AFTERWARDS with the results, the final goal of the request, \
as a phrase starting with a verb, e.g. "set insurance premiums by diabetes risk". Not the analysis.
- "affected_people": whose situation the end use will change, or "nobody"."""

EXTRACT_SCHEMA = {
    "type": "object",
    "properties": {
        "actor": {"type": "string"},
        "analysis": {"type": "string"},
        "end_use": {"type": "string"},
        "affected_people": {"type": "string"},
    },
    "required": ["actor", "analysis", "end_use", "affected_people"],
}

MATCH_PROMPT = """You check an intended use of health data against the European Health Data Space \
Regulation (EU) 2025/327. Below are purposes for which health data may be used (Article 53) and \
uses that are prohibited (Article 54). Pick the one rule that best covers what the applicant will \
actually do with the results, then copy word for word the part of that rule that covers it."""


class LLM(Protocol):
    def complete_json(self, system: str, user: str, schema: dict) -> dict: ...


@dataclass
class Match:
    point: LawParagraph | None  # None: the model named no valid rule
    verified: bool  # the copied words appear in the point's text


@cache
def _default_retriever() -> Retriever:
    return Retriever(OllamaEmbedder())


@cache
def _default_llm() -> LLM:
    return OllamaLLM()


def _normalise(text: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text.lower()).split())


def _points_of(paragraph_id: str):
    """Filter for the points of one paragraph, e.g. "Art. 54" or "Art. 53(1)"."""
    return lambda c: c.kind == "point" and c.id.startswith(paragraph_id + "(")


def point_text(point: LawParagraph) -> str:
    """A point's text without its "(b)" marker and trailing ";" or "; or", for quoting."""
    text = re.sub(r"^\([a-z]+\)\s*", "", point.text)
    return re.sub(r"(;\s*(or|and)?|,)$", "", text.strip())


def extract(request: DataRequest, llm: LLM) -> dict[str, str]:
    user = f"Organisation: {request.applicant.organisation}\nPurpose: {request.purpose}"
    return llm.complete_json(EXTRACT_PROMPT, user, EXTRACT_SCHEMA)


def match(llm: LLM, facts: dict[str, str], permitted: list[LawParagraph], prohibited: list[LawParagraph]) -> Match:
    def rules(points: list[LawParagraph]) -> str:
        return "\n".join(f"[{p.id}] {point_text(p)}" for p in points)

    user = (
        f"PERMITTED PURPOSES (Article 53(1)):\n{rules(permitted)}\n\n"
        f"PROHIBITED USES (Article 54):\n{rules(prohibited)}\n\n"
        f"APPLICANT: {facts['actor']}\nEND USE OF THE RESULTS: {facts['end_use']}\n"
        f"AFFECTED PEOPLE: {facts['affected_people']}"
    )
    candidates = permitted + prohibited
    schema = {
        "type": "object",
        "properties": {
            "rule_id": {"type": "string", "enum": [p.id for p in candidates]},
            "matching_words": {"type": "string"},
        },
        "required": ["rule_id", "matching_words"],
    }
    answer = llm.complete_json(MATCH_PROMPT, user, schema)
    point = next((p for p in candidates if p.id == answer.get("rule_id")), None)
    words = _normalise(answer.get("matching_words", ""))
    verified = point is not None and len(words.split()) >= 2 and words in _normalise(point.text)
    return Match(point, verified)


def decide(
    request: DataRequest, retriever: Retriever | None = None, llm: LLM | None = None
) -> Decision:
    retriever = retriever or _default_retriever()
    llm = llm or _default_llm()

    facts = extract(request, llm)
    query = f"{facts['actor']}: {facts['end_use']}. Affected: {facts['affected_people']}"
    result = match(
        llm,
        facts,
        retriever.search(query, RETRIEVAL_TOP_K, _points_of(PERMITTED)),
        retriever.search(query, RETRIEVAL_TOP_K, _points_of(PROHIBITED)),
    )
    use = f"Applicant: {facts['actor']}. Intended use: {facts['end_use'].rstrip('.')}."
    p = result.point

    if p and p.article == "54":
        return Decision(
            accepted=False,
            articles=["54"],
            reason=f"Rejected: prohibited secondary use under {p.id}. {use} "
            f"{p.id} prohibits “{point_text(p)}”.",
            grounding=[p],
            needs_review=not result.verified,
        )

    articles = ["53"]
    if p:
        reason = f"Accepted: permitted purpose under {p.id}. {use} {p.id} allows “{point_text(p)}”."
    else:
        reason = f"Accepted: no prohibited use under Art. 54 found. {use}"
    if request.requested_as == ACCESS_APPLICATION:
        articles.append("66")
        reason += " Handled as a health data access application (Art. 66)."
    return Decision(
        accepted=True, articles=articles, reason=reason, grounding=[p] if p else [],
        needs_review=not result.verified,
    )
