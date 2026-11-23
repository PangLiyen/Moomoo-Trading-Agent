"""N31 -- the foreign-order guard's only input had no age limit.

Cycle 5 decided on an order list captured at 10:47:56 while an order was
created at 10:52:29; the guard passed a snapshot that could not contain the
order it exists to find. A guard that attests to a state it cannot see is
worse than one that refuses, because the approval is recorded as evidence.

This is the same class of defect as the quote-staleness fix (3e4ce37) and the
bar-lookahead finding: a safety decision taken on an input of unbounded age.
"""

from datetime import datetime

import pytest

from engine.errors import StaleDataError
from engine.timeutil import assert_order_list_fresh

MAX = 120.0


def test_a_negative_age_halts():
    """A capture time after the decision time cannot be placed on the clock.
    Treating it as 'very fresh' is the one failure this must never have."""
    with pytest.raises(StaleDataError, match="negative"):
        assert_order_list_fresh(-0.001, MAX)


def test_zero_age_passes():
    assert assert_order_list_fresh(0.0, MAX) == 0.0


def test_exactly_the_limit_passes():
    """The bound is inclusive: 120 seconds is inside [0, 120]."""
    assert assert_order_list_fresh(MAX, MAX) == MAX


def test_one_second_past_the_limit_halts():
    with pytest.raises(StaleDataError, match="exceeds"):
        assert_order_list_fresh(MAX + 1.0, MAX)


def test_the_cycle_five_capture_now_halts():
    """The reproduction: the snapshot used at 10:52:44, captured at 10:47:56,
    is 288 seconds old. What the guard could not see was created mid-cycle;
    the age bound is what makes that impossible to attest to."""
    captured = datetime(2026, 11, 23, 10, 47, 56)
    used = datetime(2026, 11, 23, 10, 52, 44)
    age = (used - captured).total_seconds()
    assert age == 288.0
    with pytest.raises(StaleDataError, match="exceeds 120s"):
        assert_order_list_fresh(age, MAX)
