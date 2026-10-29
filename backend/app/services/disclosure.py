"""Disclosure control: the small-cell rule for every statistical answer.

- Every requested group is shown, even if empty.
- Counts below 10, including 0, are shown as "<10".
- A percentage whose numerator or denominator is below 10 is shown as "suppressed".
- A mean over fewer than 10 patients is shown as "suppressed".
- Values are rounded to one decimal, halves up (not Python's banker's rounding).

The threshold is settings.small_cell_threshold.
"""

from typing import Any


def round_half_up(value: float) -> float:
    """Round to one decimal, halves up."""
    raise NotImplementedError


def mask_count(count: int) -> int | str:
    """Return the count, or "<10" if it is below the threshold."""
    raise NotImplementedError


def mask_percentage(numerator: int, denominator: int) -> float | str:
    """Return the rounded percentage, or "suppressed" if either part is below 10."""
    raise NotImplementedError


def mask_mean(mean: float, patients: int) -> float | str:
    """Return the rounded mean, or "suppressed" if fewer than 10 patients contribute."""
    raise NotImplementedError


def apply(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Mask a raw result table into the answer sent to the applicant."""
    raise NotImplementedError
