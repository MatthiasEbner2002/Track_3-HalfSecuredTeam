"""Decision system (grounded RAG): accept or reject a request and cite articles.

STUB: accepts every request. To replace: reject prohibited purposes (Art. 54)
and cite the articles that justify the decision.
"""

from app.models import DataRequest, Decision


def decide(request: DataRequest) -> Decision:
    return Decision(accepted=True, articles=[], reason="Stub: every request is accepted.")
