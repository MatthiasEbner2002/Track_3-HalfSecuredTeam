"""Small-cell rule: counts below 10 -> "<10", percentages and means over fewer than 10 -> "suppressed"."""

from app.config import SMALL_CELL_THRESHOLD


def count(n: int) -> int | str:
    return "<10" if n < SMALL_CELL_THRESHOLD else n


def derived(value: float | None, based_on: int) -> float | str:
    """A mean over `based_on` patients, or a percentage with `based_on` = min(numerator, denominator)."""
    return "suppressed" if value is None or based_on < SMALL_CELL_THRESHOLD else value

