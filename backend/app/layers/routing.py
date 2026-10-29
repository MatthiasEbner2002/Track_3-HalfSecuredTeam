"""Choose between statistics (default) and SPE for an accepted request.

Decision order (challenge README, Regulation (EU) 2025/327 Chapter IV):
- health data request                              -> statistics, Art. 69
- access application that explains why statistics
  are not enough (patient-level data needed)       -> SPE, Art. 66, 68, 73
- any other access application                     -> statistics, Art. 66, 69 (downgrade)
"""

from app.models import DataRequest, Decision, Route
from app.purpose import assess
from app.purpose.assess import config

HEALTH_DATA_REQUEST = "health data request"


def choose_route(request: DataRequest, decision: Decision) -> Route:
    if request.requested_as == HEALTH_DATA_REQUEST:
        return "statistics"
    return "spe" if assess(request).spe_justification else "statistics"


def explain(request: DataRequest, route: Route) -> tuple[list[str], str]:
    """Articles and reason text that the route adds to the decision."""
    reasons = config()["reasons"]
    if route == "spe":
        return ["66", "68", "73"], reasons["spe"]
    if request.requested_as == HEALTH_DATA_REQUEST:
        return ["69"], reasons["statistical"]
    return ["66", "69"], reasons["downgrade"]
