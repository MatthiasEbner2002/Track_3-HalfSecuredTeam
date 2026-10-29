"""Tests for the disclosure control (small-cell rule)."""

import pytest

from app.services.disclosure import mask_count, mask_mean, mask_percentage, round_half_up


class TestRoundHalfUp:
    def test_rounds_down(self):
        assert round_half_up(1.44) == 1.4

    def test_rounds_up_on_half(self):
        assert round_half_up(1.45) == 1.5

    def test_already_one_decimal(self):
        assert round_half_up(2.3) == 2.3

    def test_negative(self):
        assert round_half_up(-1.45) == -1.4  # floor(-14.5) + 0.5 = -14 → -1.4

    def test_integer(self):
        assert round_half_up(7.0) == 7.0


class TestMaskCount:
    def test_below_threshold_returns_string(self):
        assert mask_count(0) == "<10"
        assert mask_count(1) == "<10"
        assert mask_count(9) == "<10"

    def test_at_threshold_returns_int(self):
        assert mask_count(10) == 10

    def test_above_threshold_returns_int(self):
        assert mask_count(100) == 100


class TestMaskPercentage:
    def test_small_numerator_suppressed(self):
        assert mask_percentage(5, 100) == "suppressed"

    def test_small_denominator_suppressed(self):
        assert mask_percentage(100, 5) == "suppressed"

    def test_both_small_suppressed(self):
        assert mask_percentage(5, 5) == "suppressed"

    def test_normal_rounding(self):
        assert mask_percentage(33, 100) == 33.0

    def test_rounding_half_up(self):
        # 37/100 * 100 = 37.0 — just a normal round
        assert mask_percentage(37, 100) == 37.0

    def test_exact_one_decimal(self):
        # 71/885 = 8.022... rounds to 8.0
        assert mask_percentage(71, 885) == 8.0

    def test_at_threshold_both(self):
        # 10/10 = 100% — both exactly at threshold, should work
        assert mask_percentage(10, 10) == 100.0


class TestMaskMean:
    def test_too_few_patients_suppressed(self):
        assert mask_mean(27.2, 9) == "suppressed"
        assert mask_mean(27.2, 0) == "suppressed"

    def test_enough_patients_returns_rounded(self):
        assert mask_mean(27.23, 15) == 27.2

    def test_exactly_threshold(self):
        assert mask_mean(26.5, 10) == 26.5
