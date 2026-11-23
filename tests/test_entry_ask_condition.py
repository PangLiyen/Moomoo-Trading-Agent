"""N29 -- the registered rule must hold at the price actually paid.

`docs/STRATEGY.md` section 4 registers the entry as price dislocated below
SMA20 by 1.5 ATR. The gate tested the last closed bar against that threshold,
but the order is a crossing limit: it prices near the ask, which on a falling
bar sits above the close. The 2026-11-23 XLE case had a bar close of 60.35
(the threshold was 60.3688) and an ask of 60.42, so a fill would have been
0.0512 outside the registered rule while every recorded condition said it was
inside.

Adding the ask condition can only refuse entries that were previously taken.
It cannot create one, which is why it is not a strategy change.
"""

from datetime import datetime

import pytest

from engine.signal import Indicators, evaluate_entry

NOW = datetime(2026, 11, 23, 10, 30)


def _ind(close=60.35, prev_close=60.30, sma20=63.3688, atr=2.0):
    return Indicators(symbol="US.XLE", as_of_et=NOW, close=close,
                      prev_close=prev_close, sma20=sma20, atr20=atr,
                      bars_used=250)


def _eval(ask, ind=None):
    return evaluate_entry(ind or _ind(), regime_ok=True, live_ask=ask,
                          quote_age_seconds=3.0, spread_pct_of_mid=0.004,
                          now_et=NOW)


def test_the_2026_11_23_xle_case_is_refused_at_the_fill_price():
    v = _eval(60.42)
    assert v.qualifies is False
    assert v.reason_code == "NO_SETUP"
    assert v.conditions["dislocated_below_sma20"] is True
    assert v.conditions["ticked_up"] is True
    assert v.conditions["regime_ok"] is True
    assert v.conditions["ask_below_threshold"] is False
    assert v.reason_text == "failed: ask_below_threshold"
    assert v.detail["entry_threshold"] == pytest.approx(60.3688)
    assert v.detail["live_ask"] == pytest.approx(60.42)
    assert v.detail["ask_distance_below_threshold"] == pytest.approx(-0.0512)


def test_an_ask_below_the_threshold_permits_the_entry():
    """The same bar, the same indicator values: only the price paid differs."""
    v = _eval(60.36)
    assert v.qualifies is True
    assert v.reason_code == "SETUP"
    assert all(v.conditions.values())
    assert v.detail["ask_distance_below_threshold"] == pytest.approx(0.0088)


def test_an_ask_exactly_on_the_threshold_is_refused():
    """The rule is 'dislocated below', so equality is not inside it."""
    v = _eval(60.3688)
    assert v.conditions["ask_below_threshold"] is False


@pytest.mark.parametrize("ask", [0.0, -1.0])
def test_a_missing_or_negative_ask_fails_closed(ask):
    """No quote is not a cheap quote. It reads as NO_SETUP, not as an error
    that could be swallowed, and certainly not as a pass."""
    v = _eval(ask)
    assert v.qualifies is False
    assert v.conditions["ask_below_threshold"] is False
