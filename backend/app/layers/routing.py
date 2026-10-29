"""Choose between statistics (default) and SPE for an accepted request.

STUB: always "statistics". To replace: route to "spe" only when an access
application explains why statistics are not enough (LLM and/or rules).
"""

from app.models import DataRequest, Decision, Route


def choose_route(request: DataRequest, decision: Decision) -> Route:
    return "statistics"
