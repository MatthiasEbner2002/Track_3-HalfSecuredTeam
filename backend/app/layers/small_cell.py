"""Small-cell rule applied to the results before they leave the system.

STUB: returns the results unchanged. To replace (statistics route): counts
below 10 -> "<10", percentages and means with fewer than 10 -> "suppressed",
round to one decimal, halves up.
"""

from app.models import QueryResults, Route


def apply(results: QueryResults, route: Route) -> QueryResults:
    return results
