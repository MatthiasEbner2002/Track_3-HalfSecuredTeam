"""Decision: REJECT / APPROVE_STATISTICAL / APPROVE_SPE with cited articles.

Checked in this order (Regulation (EU) 2025/327, Chapter IV):

1. Prohibited purpose                            -> REJECT, Art. 54
2. Health data request                           -> APPROVE_STATISTICAL, Art. 53, 69
3. Access application that justifies why
   statistics are not enough                     -> APPROVE_SPE, Art. 53, 66, 68, 73
4. Any other access application                  -> APPROVE_STATISTICAL, Art. 53, 66, 69
"""

from app.models.schemas import DataRequest, Decision


def decide(request: DataRequest) -> Decision:
    """Return the decision, the cited articles and a reason the applicant can read."""
    raise NotImplementedError
