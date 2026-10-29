"""Decision: REJECT / APPROVE_STATISTICAL / APPROVE_SPE with cited articles.

Checked in this order (Regulation (EU) 2025/327, Chapter IV):

1. Prohibited purpose                            -> REJECT, Art. 54
2. Health data request                           -> APPROVE_STATISTICAL, Art. 53, 69
3. Access application that justifies why
   statistics are not enough                     -> APPROVE_SPE, Art. 53, 66, 68, 73
4. Any other access application                  -> APPROVE_STATISTICAL, Art. 53, 66, 69
"""

import re

from app.models.schemas import DataRequest, Decision, DecisionType, RequestedAs

# ---------------------------------------------------------------------------
# Art. 54 – prohibited purposes (keywords / phrases)
# ---------------------------------------------------------------------------

_PROHIBITED_PATTERNS: list[re.Pattern[str]] = [
    # Insurance pricing / underwriting
    re.compile(r"insur(ance)?\s*(premium|pric|underwr|risk\s+assess)", re.I),
    re.compile(r"premium", re.I),
    # Advertising / marketing / commercial promotion of products
    re.compile(r"\badvertis", re.I),
    re.compile(r"\bmarketing\b", re.I),
    re.compile(r"\bphysician\s+advertis", re.I),
    re.compile(r"\bpromotion(al)?\b", re.I),
    # Employment decisions
    re.compile(r"\bemploy(ee|ment|er)\b", re.I),
    re.compile(r"\bworkplace\b", re.I),
    # Tobacco / nicotine product development or marketing
    re.compile(r"\bnicotine\b", re.I),
    re.compile(r"\btobacco\b", re.I),
    re.compile(r"\bsmoking\s+(product|cessation\s+product)", re.I),
    # Re-identification / linking with external lists
    re.compile(r"\bemployee\s+list\b", re.I),
]

# Phrases that override a false positive – when these appear in the purpose,
# the request is NOT prohibited even if a banned keyword is incidentally present.
_OVERRIDE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"\bquit\s+smoking\b", re.I),
    re.compile(r"\bsmoking\s+cessation\s+(programme|program|service|advice)\b", re.I),
]

# Phrases that signal the applicant explicitly says statistics are not enough
_STATS_NOT_ENOUGH_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"(statistic|aggregate)(s)?\s+(are|is)?\s+not\s+enough", re.I),
    re.compile(r"individual.{0,10}(level|record|history|histories)", re.I),
    re.compile(r"patient.{0,10}level", re.I),
    re.compile(r"needs?\s+individual", re.I),
    re.compile(r"aggregates?\s+are\s+not\s+(enough|sufficient)", re.I),
    re.compile(r"patient.{0,10}histories", re.I),
    re.compile(r"training\s+(need|requires?|uses?)\s+individual", re.I),
]


def _is_prohibited(request: DataRequest) -> bool:
    """Return True if the request purpose or question matches a prohibited pattern."""
    text = f"{request.purpose} {request.question} {' '.join(request.definitions)}"
    # Check overrides first
    for pat in _OVERRIDE_PATTERNS:
        if pat.search(text):
            return False
    for pat in _PROHIBITED_PATTERNS:
        if pat.search(text):
            return True
    return False


def _justifies_spe(request: DataRequest) -> bool:
    """Return True if the applicant explicitly explains why statistics are not enough."""
    text = f"{request.purpose} {request.question} {' '.join(request.definitions)}"
    for pat in _STATS_NOT_ENOUGH_PATTERNS:
        if pat.search(text):
            return True
    return False


def decide(request: DataRequest) -> Decision:
    """Return the decision, the cited articles and a reason the applicant can read."""

    # 1. Prohibited purpose → REJECT
    if _is_prohibited(request):
        return Decision(
            decision=DecisionType.REJECT,
            articles=["54"],
            reason=_reject_reason(request),
        )

    # 2. Health data request (always → APPROVE_STATISTICAL)
    if request.requested_as == RequestedAs.HEALTH_DATA_REQUEST:
        return Decision(
            decision=DecisionType.APPROVE_STATISTICAL,
            articles=["53", "69"],
            reason=(
                "Research and public health are allowed purposes (Art. 53). "
                "Statistics answer the question (Art. 69)."
            ),
        )

    # 3 & 4: Health data access application
    if _justifies_spe(request):
        return Decision(
            decision=DecisionType.APPROVE_SPE,
            articles=["53", "66", "68", "73"],
            reason=(
                "Training and testing algorithms is an allowed purpose (Art. 53). "
                "The model needs patient-level data, so pseudonymised data is justified (Art. 66). "
                "Access only under a data permit inside a secure processing environment (Art. 68, 73)."
            ),
        )

    # 4. Access application where statistics would suffice
    return Decision(
        decision=DecisionType.APPROVE_STATISTICAL,
        articles=["53", "66", "69"],
        reason=(
            "Teaching and research are allowed purposes (Art. 53). "
            "Statistics answer the question, so pseudonymised records are not justified (Art. 66). "
            "Approved as statistics instead (Art. 69)."
        ),
    )


def _reject_reason(request: DataRequest) -> str:
    """Generate a readable rejection reason."""
    text = request.purpose.lower()
    if "insur" in text or "premium" in text:
        return "Using health data to set insurance premiums is prohibited (Art. 54)."
    if "advertis" in text or "marketing" in text or "campaign" in text:
        return "Advertising and marketing are prohibited (Art. 54)."
    if "employ" in text or "employee" in text:
        return (
            "Using health data for decisions about employees is prohibited (Art. 54). "
            "Matching it with an employee list would also re-identify people."
        )
    if "nicotine" in text or "tobacco" in text:
        return "Developing or marketing nicotine products is prohibited (Art. 54)."
    return "The stated purpose is prohibited under Art. 54."
