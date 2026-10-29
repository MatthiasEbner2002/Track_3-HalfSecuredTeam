"""Disclosure control: the small-cell rule for every statistical answer.

- Every requested group is shown, even if empty.
- Counts below 10, including 0, are shown as "<10".
- A percentage whose numerator or denominator is below 10 is shown as "suppressed".
- A mean over fewer than 10 patients is shown as "suppressed".
- Values are rounded to one decimal, halves up (not Python's banker's rounding).

The threshold is settings.small_cell_threshold (default 10).
"""

import math
from typing import Any

from app.config import settings

THRESHOLD = settings.small_cell_threshold  # 10


def round_half_up(value: float, decimals: int = 1) -> float:
    """Round to `decimals` decimal places, halves up (0.5 → away from zero)."""
    factor = 10**decimals
    return math.floor(value * factor + 0.5) / factor


def mask_count(count: int) -> int | str:
    """Return the count, or "<10" if it is below the threshold."""
    if count < THRESHOLD:
        return "<10"
    return count


def mask_percentage(numerator: int, denominator: int) -> float | str:
    """Return the rounded percentage, or "suppressed" if either part is below threshold."""
    if numerator < THRESHOLD or denominator < THRESHOLD:
        return "suppressed"
    return round_half_up(numerator / denominator * 100)


def mask_mean(mean: float, patients: int) -> float | str:
    """Return the rounded mean, or "suppressed" if fewer than threshold patients contribute."""
    if patients < THRESHOLD:
        return "suppressed"
    return round_half_up(mean)


def apply(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Mask a raw result table into the answer sent to the applicant.

    Each row must have a 'group' key plus typed metric keys. Masking is applied
    per row based on the metric types present.

    Metric key conventions (set by engine.py):
      _count_<name>   – an integer count, masked by mask_count
      _pct_<name>_num / _pct_<name>_den – numerator/denominator pair → masked percentage
      _mean_<name>_val / _mean_<name>_n  – mean value + patient count → masked mean
    """
    result: dict[str, Any] = {}
    for row in rows:
        group = row["group"]
        masked: dict[str, Any] = {}

        # Collect raw metrics
        for key, val in row.items():
            if key == "group":
                continue
            if key.startswith("_count_"):
                metric = key[len("_count_"):]
                masked[metric] = mask_count(int(val))
            # pct pairs are resolved together below
            elif key.startswith("_pct_") and key.endswith("_num"):
                metric = key[len("_pct_"):-len("_num")]
                den_key = f"_pct_{metric}_den"
                masked[metric] = mask_percentage(int(val), int(row.get(den_key, 0)))
            elif key.startswith("_pct_") and key.endswith("_den"):
                pass  # handled in _num branch
            elif key.startswith("_mean_") and key.endswith("_val"):
                metric = key[len("_mean_"):-len("_val")]
                n_key = f"_mean_{metric}_n"
                masked[metric] = mask_mean(float(val), int(row.get(n_key, 0)))
            elif key.startswith("_mean_") and key.endswith("_n"):
                pass  # handled in _val branch
            else:
                # Pass through unknown keys unchanged
                masked[key] = val

        if len(masked) == 1:
            # Single-metric answers are returned as scalar values, not dicts
            result[group] = next(iter(masked.values()))
        else:
            result[group] = masked

    return result
