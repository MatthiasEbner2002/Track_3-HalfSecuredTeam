"""Decision system: reject prohibited purposes (Art. 54), otherwise accept under Art. 53.

The purpose check (app/purpose) scores the request against each article point
with a local NLI model, with a few unambiguous rules as a floor. The route
(statistics or SPE) and its articles are added by app/layers/routing.py.
"""

from app.models import DataRequest, Decision
from app.purpose import assess
from app.purpose.assess import config


def _join(labels: list[str]) -> str:
    labels = list(dict.fromkeys(labels))
    return labels[0] if len(labels) == 1 else ", ".join(labels[:-1]) + " and " + labels[-1]


def decide(request: DataRequest) -> Decision:
    result = assess(request)
    reasons = config()["reasons"]

    if result.prohibited:
        return Decision(
            accepted=False,
            articles=["54"],
            reason=reasons["reject"].format(labels=_join([e.label for e in result.prohibited])),
            evidence=result.prohibited,
            flags=result.flags,
        )

    if result.allowed:
        labels = _join([e.label for e in result.allowed])
        verb = "are allowed purposes" if len(result.allowed) > 1 else "is an allowed purpose"
        text = reasons["allowed"].format(labels=labels, verb=verb)
        reason = text[0].upper() + text[1:]
    else:
        reason = reasons["no_allowed_purpose"]
    return Decision(accepted=True, articles=["53"], reason=reason, evidence=result.allowed, flags=result.flags)
