"""The freshness assertion: both ends of [0, max] are enforced.

A one-sided `age < max` comparison passes a negative age trivially. That is
not hypothetical: the first quote-age pass on 2026-11-23 applied the ET offset
twice and produced ages near -5 hours, which the old comparison would have
accepted as five-hour-old books. These tests pin the lower bound, the upper
bound, and the single conversion every call site now uses.
"""

from datetime import datetime

import pytest

from engine.errors import StaleDataError
from engine.signal import Indicators, evaluate_entry
from engine.sizing import size_position
from engine.timeutil import assert_quote_fresh, epoch_ms_to_et, quote_age_seconds

from conftest import MEASURED


def _ind():
    return Indicators(symbol="US.SPY", as_of_et=datetime(2026, 11, 23, 10, 25),
                      close=700.0, prev_close=699.0, sma20=716.0, atr20=2.0,
                      bars_used=250)


def test_negative_age_halts():
    with pytest.raises(StaleDataError, match="negative"):
        assert_quote_fresh(-18000.0, 120.0)


def test_zero_age_passes():
    assert assert_quote_fresh(0.0, 120.0) == 0.0


def test_max_age_passes():
    assert assert_quote_fresh(120.0, 120.0) == 120.0


def test_max_plus_one_halts():
    with pytest.raises(StaleDataError, match="exceeds"):
        assert_quote_fresh(121.0, 120.0)


def test_epoch_conversion_is_et_wall_clock():
    # Measured bar time_key: 2026-11-16 16:15 UTC, i.e. 11:15 ET.
    assert epoch_ms_to_et(1794845700000) == datetime(2026, 11, 16, 11, 15)
    # Order-book timestamp for the 10:26:33 ET book read on 2026-11-23.
    assert epoch_ms_to_et(1795447593429) == datetime(2026, 11, 23, 10, 26, 33, 429000)


def test_quote_age_is_non_negative_for_a_book_in_the_present():
    now = datetime(2026, 11, 23, 10, 26, 38)
    age = quote_age_seconds(1795447593429, now)
    assert age == pytest.approx(4.571, abs=1e-3)


def test_driver_bar_parsing_uses_the_single_helper():
    import run_cycle

    assert run_cycle._parse_ts(1794845700000) == epoch_ms_to_et(1794845700000)


def test_entry_rule_refuses_a_negative_age():
    with pytest.raises(StaleDataError):
        evaluate_entry(_ind(), regime_ok=True, live_ask=699.9,
                       quote_age_seconds=-1.0,
                       spread_pct_of_mid=0.005,
                       now_et=datetime(2026, 11, 23, 10, 25))


def test_entry_rule_refuses_an_age_past_the_limit():
    with pytest.raises(StaleDataError):
        evaluate_entry(_ind(), regime_ok=True, live_ask=699.9,
                       quote_age_seconds=121.0,
                       spread_pct_of_mid=0.005,
                       now_et=datetime(2026, 11, 23, 10, 25))


def test_risk_gate_raises_on_a_negative_age(gate, cfg, clean_account,
                                            open_session):
    px, atr = MEASURED["US.SPY"]
    proposal = size_position(cfg, symbol="US.SPY", price=px, atr20=atr,
                             equity=clean_account.total_asset)
    with pytest.raises(StaleDataError, match="negative"):
        gate.evaluate_entry(proposal, acct=clean_account, sess=open_session,
                            quote_age_seconds=-5.0, spread_pct_of_mid=0.005)
