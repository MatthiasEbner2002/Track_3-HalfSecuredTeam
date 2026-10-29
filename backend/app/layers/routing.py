"""Choose between statistics (default) and SPE for an accepted request.

STUB: always "statistics". To replace: route to "spe" only when an access
application explains why statistics are not enough (LLM and/or rules).
"""

from app.models import DataRequest, Decision, Route

# Articles added on top of the decision layer's, per route.
ROUTE_ARTICLES: dict[Route, list[str]] = {"statistics": ["69"], "spe": ["68", "73"]}


def choose_route(request: DataRequest, decision: Decision) -> Route:
    return "statistics"
