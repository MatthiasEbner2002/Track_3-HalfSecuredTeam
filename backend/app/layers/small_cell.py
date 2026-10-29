"""Small-cell rule applied to the results before they leave the system.

STUB: returns the results unchanged. To replace (statistics route): counts
below 10 -> "<10", percentages and means with fewer than 10 -> "suppressed",
round to one decimal, halves up.
"""

from app.config import SMALL_CELL_THRESHOLD
from app.models import QueryResults, Route


def count(n: int) -> int | str:
    return "<10" if n < SMALL_CELL_THRESHOLD else n


def derived(value: float | None, based_on: int) -> float | str:
    """A mean over `based_on` patients, or a percentage with `based_on` = min(numerator, denominator)."""
    return "suppressed" if value is None or based_on < SMALL_CELL_THRESHOLD else value


def apply(results: QueryResults, route: Route) -> QueryResults:
    return results
