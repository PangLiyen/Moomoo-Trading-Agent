"""Tests for the exit path, the calendar, and the records that make the
pre-registered predictions measurable.

Written against the behaviour the driver needs, not only against the
functions. Every defect found in this project so far has been a control whose
logic was correct and whose caller starved it, and function-level tests that
construct their own inputs cannot see that.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from engine.executor import (
    ExitIntent, build_order, crossing_limit, implied_fee, plan_exits,
    plan_session, record_account_snapshot, record_exit, record_fill,
    record_submission, write_intents,
)
from engine.ledger import Ledger
from engine.signal import Indicators, Position

from conftest import MEASURED


def _pos(symbol="US.SPY", qty=20, entry=712.50, atr=2.10, mins_ago=10):
    return Position(symbol=symbol, qty=qty, entry_price=entry,
                    entry_atr20=atr,
                    entry_time_et=datetime(2026, 11, 23, 11, 0) -
                    timedelta(minutes=mins_ago))


def _ind(symbol="US.SPY", close=712.0, sma20=714.0, atr=2.10):
    return Indicators(symbol=symbol, as_of_et=datetime(2026, 11, 23, 11, 0),
                      close=close, prev_close=close - 0.1, sma20=sma20,
                      atr20=atr, bars_used=250)


# --------------------------------------------------------------------------
# The calendar -- config, not defaults
# --------------------------------------------------------------------------

def test_thanksgiving_is_not_a_trading_day(cfg):
    plan = plan_session(cfg, datetime(2026, 11, 26, 10, 0))
    assert plan.is_trading_day is False
    assert plan.status == "closed"


def test_the_27_nov_half_day_pulls_both_the_flat_by_and_the_entry_cutoff(cfg):
    """The deck warns there are no reset cards and that unmanaged holiday gaps
    can force liquidation. A 13:00 close with a 15:50 flat-by would hold a
    position past the close; a 15:00 entry cutoff would open one 20 minutes
    before it with a 60-minute time stop that cannot run."""
    plan = plan_session(cfg, datetime(2026, 11, 27, 10, 0))
    assert plan.status == "half_day"
    assert plan.market_close_et.isoformat(timespec="minutes") == "13:00"
    assert plan.flat_by_et.isoformat(timespec="minutes") == "12:45"
    assert plan.latest_entry_et <= plan.flat_by_et


def test_a_regular_day_uses_the_session_block(cfg):
    plan = plan_session(cfg, datetime(2026, 11, 23, 10, 0))
    assert (plan.earliest_entry_et.isoformat(timespec="minutes"),
            plan.flat_by_et.isoformat(timespec="minutes")) == ("10:00", "15:50")


def test_weekends_are_not_trading_days(cfg):
    assert plan_session(cfg, datetime(2026, 11, 22, 10, 0)).is_trading_day is False


# --------------------------------------------------------------------------
# Exits -- all five, each labelled
# --------------------------------------------------------------------------

def test_stop_hit_is_detected_and_marked_immediate(cfg):
    plan = plan_session(cfg, datetime(2026, 11, 23, 11, 0))
    pos = _pos()
    stop = pos.entry_price - 1.0 * pos.entry_atr20
    intents, _ = plan_exits([pos], {"US.SPY": _ind(close=stop - 0.05)}, cfg,
                            now_et=datetime(2026, 11, 23, 11, 0), plan=plan)
    assert len(intents) == 1
    assert intents[0].exit_rule == "STOP_HIT"
    assert intents[0].urgency == "immediate"


def test_target_hit_is_detected(cfg):
    plan = plan_session(cfg, datetime(2026, 11, 23, 11, 0))
    intents, _ = plan_exits([_pos()], {"US.SPY": _ind(close=714.5, sma20=714.0)},
                            cfg, now_et=datetime(2026, 11, 23, 11, 0), plan=plan)
    assert intents[0].exit_rule == "TARGET_HIT"


def test_time_stop_fires_at_the_configured_limit(cfg):
    plan = plan_session(cfg, datetime(2026, 11, 23, 13, 0))
    limit = int(cfg.section("execution")["max_holding_minutes"])
    intents, _ = plan_exits([_pos(mins_ago=limit + 1)],
                            {"US.SPY": _ind(close=713.0)}, cfg,
                            now_et=datetime(2026, 11, 23, 13, 0), plan=plan)
    assert intents[0].exit_rule == "TIME_STOP"


def test_session_stop_fires_and_outranks_everything(cfg):
    """A bar past the deadline and below the stop is labelled SESSION_STOP:
    the deadline is why we are leaving, regardless of price."""
    plan = plan_session(cfg, datetime(2026, 11, 23, 15, 55))
    pos = _pos()
    intents, _ = plan_exits([pos], {"US.SPY": _ind(close=pos.entry_price - 99)},
                            cfg, now_et=datetime(2026, 11, 23, 15, 55), plan=plan)
    assert intents[0].exit_rule == "SESSION_STOP"
    assert intents[0].urgency == "immediate"


def test_holiday_flat_by_fires_at_1245_not_1550(cfg):
    plan = plan_session(cfg, datetime(2026, 11, 27, 12, 46))
    intents, _ = plan_exits([_pos(mins_ago=5)], {"US.SPY": _ind(close=713.0)},
                            cfg, now_et=datetime(2026, 11, 27, 12, 46), plan=plan)
    assert intents[0].exit_rule == "SESSION_STOP", (
        "on the 27th a position must be flat by 12:45, not 15:50"
    )


def test_a_holding_position_still_produces_a_verdict(cfg):
    """A HOLD is a decision about a live position. A log recording only the
    cycles where something happened cannot show the system was watching."""
    plan = plan_session(cfg, datetime(2026, 11, 23, 11, 0))
    intents, verdicts = plan_exits([_pos()], {"US.SPY": _ind(close=713.0)}, cfg,
                                   now_et=datetime(2026, 11, 23, 11, 0), plan=plan)
    assert intents == []
    assert len(verdicts) == 1 and verdicts[0].reason_code == "HOLD"


def test_a_position_with_no_data_exits_immediately_rather_than_holding(cfg):
    """Unevaluable is worse than exiting. The stop is software-enforced, so no
    data means no protection."""
    plan = plan_session(cfg, datetime(2026, 11, 23, 11, 0))
    intents, _ = plan_exits([_pos()], {}, cfg,
                            now_et=datetime(2026, 11, 23, 11, 0), plan=plan)
    assert intents[0].exit_rule == "NO_DATA"
    assert intents[0].urgency == "immediate"


# --------------------------------------------------------------------------
# Order construction
# --------------------------------------------------------------------------

def test_order_matches_the_measured_schema(cfg):
    o = build_order(cfg, symbol="US.SPY", side="SELL", qty=20,
                    limit_price=705.12, cycle_id="C20261123T1030",
                    ledger_tip="a1b2c3d4e5f6")
    assert o["order_type"] == 1              # enum is {1 Limit, 3 Market}
    assert o["order_side"] == 2
    assert o["order_trade_time_type"] == 1   # intraday only, by construction
    assert o["qty"] == "20" and isinstance(o["qty"], str)
    assert o["acc_id"] == cfg.acc_id


def test_the_ledger_hash_is_stamped_into_the_broker_record(cfg):
    """The one hole the chain cannot close itself: truncating its tail leaves
    a self-consistent file. The broker still holds hashes for orders the
    ledger no longer mentions."""
    o = build_order(cfg, symbol="US.QQQ", side="BUY", qty=23,
                    limit_price=640.0, cycle_id="C20261123T1105",
                    ledger_tip="deadbeefcafe")
    assert "deadbeef" in o["text"]
    assert len(o["text"].encode()) <= 100


def test_order_rejects_nonsense(cfg):
    with pytest.raises(ValueError):
        build_order(cfg, symbol="US.SPY", side="HOLD", qty=1, limit_price=1.0,
                    cycle_id="c", ledger_tip="x")
    with pytest.raises(ValueError):
        build_order(cfg, symbol="US.SPY", side="BUY", qty=0, limit_price=1.0,
                    cycle_id="c", ledger_tip="x")


def test_immediate_exits_price_through_the_book(cfg):
    """A stop that does not fill is not a stop."""
    normal = crossing_limit(cfg, side="SELL", bid=700.0, ask=700.05, urgency="normal")
    urgent = crossing_limit(cfg, side="SELL", bid=700.0, ask=700.05, urgency="immediate")
    assert urgent < normal == 700.0


# --------------------------------------------------------------------------
# Records -- P2, P6, P7
# --------------------------------------------------------------------------

def test_implied_fee_reproduces_the_measured_sell(cfg):
    """The actual Session 00 numbers: balance 99285.340 -> 99990.765 on a
    1-share sell filled at 706.425 implies exactly 1.0000."""
    fee = implied_fee(cash_before=99285.340, cash_after=99990.765,
                      filled_qty=1, avg_fill_price=706.425, side="SELL")
    assert abs(fee - 1.0) < 1e-6


def test_submission_record_captures_approved_and_submitted_qty(tmp_path, cfg):
    """Section 8 forbids the executor increasing size. Nothing could verify it
    before, because the approval and the submission were never written down
    together."""
    led = Ledger(tmp_path / "d.jsonl")
    o = build_order(cfg, symbol="US.SPY", side="BUY", qty=20, limit_price=712.0,
                    cycle_id="C1", ledger_tip="abc")
    rec = record_submission(led, cycle_id="C1", order=o, intent="ENTRY",
                            approved_qty=20, order_id="999")
    assert rec["approved_qty"] == rec["submitted_qty"] == 20
    assert rec["qty_matches_approval"] is True
    assert rec["order_id"] == "999"

    o2 = dict(o, qty="25")
    bad = record_submission(led, cycle_id="C1", order=o2, intent="ENTRY",
                            approved_qty=20, order_id="1000")
    assert bad["qty_matches_approval"] is False, (
        "an oversized submission must be detectable from the record alone"
    )


def test_fill_record_labels_price_improvement_not_slippage(tmp_path, cfg):
    led = Ledger(tmp_path / "d.jsonl")
    rec = record_fill(led, cycle_id="C1", order_id="1", symbol="US.SPY",
                      side="BUY", verdict="FILLED", filled_qty=20,
                      avg_fill_price=711.50, limit_price=712.00,
                      fill_time_et="2026-11-23T10:30:07",
                      cash_before=100000.0, cash_after=85769.01)
    assert rec["price_improvement_vs_limit"] == pytest.approx(0.50)
    assert "slippage" not in json.dumps(rec), (
        "fill-vs-limit is improvement; logging it as slippage would show the "
        "agent beating the market on every trade"
    )
    assert rec["implied_fee"] == pytest.approx(0.99, abs=1e-6)


def test_exit_record_carries_the_rule_and_the_breach(tmp_path, cfg):
    """P7 needs the rule. Without it a stop cannot be distinguished from a
    time exit that printed below the stop level."""
    led = Ledger(tmp_path / "d.jsonl")
    intent = ExitIntent(symbol="US.SPY", qty=20, exit_rule="STOP_HIT",
                        reason_text="close below stop", stop_level=710.40,
                        target_level=714.0, entry_price=712.50,
                        entry_atr20=2.10, held_minutes=25.0,
                        reference_price=709.88, urgency="immediate")
    rec = record_exit(led, cycle_id="C1", intent=intent, exit_price=709.88,
                      exit_time_et="2026-11-23T11:25:10", filled_qty=20,
                      fees=1.0)
    assert rec["exit_rule"] == "STOP_HIT"
    assert rec["breach_past_stop_atr"] == pytest.approx((710.40 - 709.88) / 2.10)
    assert rec["realised_pnl"] == pytest.approx((709.88 - 712.50) * 20 - 1.0)


def test_non_stop_exits_carry_no_breach_figure(tmp_path, cfg):
    led = Ledger(tmp_path / "d.jsonl")
    intent = ExitIntent(symbol="US.SPY", qty=20, exit_rule="TARGET_HIT",
                        reason_text="reached SMA20", stop_level=710.40,
                        target_level=714.0, entry_price=712.50,
                        entry_atr20=2.10, held_minutes=25.0,
                        reference_price=714.1, urgency="normal")
    rec = record_exit(led, cycle_id="C1", intent=intent, exit_price=714.1,
                      exit_time_et="2026-11-23T11:25:10", filled_qty=20)
    assert rec["breach_past_stop_atr"] is None


# --------------------------------------------------------------------------
# The architectural boundary
# --------------------------------------------------------------------------

def test_executor_cannot_reach_the_network():
    """The submission's central claim: the component that decides what to
    trade has no ability to trade. Enforced by structure, not intention."""
    src = (Path(__file__).resolve().parents[1] / "engine" / "executor.py").read_text()
    for forbidden in ("import requests", "import urllib", "import http",
                      "import socket", "urlopen", "requests.", "httpx"):
        assert forbidden not in src, f"network access appeared in executor: {forbidden}"


def test_intents_are_written_as_a_file_not_submitted(tmp_path, cfg):
    o = build_order(cfg, symbol="US.SPY", side="SELL", qty=20,
                    limit_price=705.0, cycle_id="C1", ledger_tip="abc")
    p = write_intents(tmp_path / "out" / "intents_C1.json", "C1", [o])
    payload = json.loads(p.read_text())
    assert payload["cycle_id"] == "C1"
    assert payload["orders"][0]["symbol"] == "US.SPY"


# --------------------------------------------------------------------------
# Regressions from the 2026-11-23 rehearsal
# --------------------------------------------------------------------------

def test_a_weekend_is_labelled_a_weekend_not_a_regular_session(cfg):
    """The rehearsal wrote `2026-11-22 is regular` with is_trading_day false
    into a hash-chained record. A self-contradictory record cannot be quietly
    corrected later, which is the point of the chain."""
    for day, name in ((22, "Sunday"), (21, "Saturday")):
        plan = plan_session(cfg, datetime(2026, 11, day, 10, 0))
        assert plan.is_trading_day is False
        assert plan.status == "weekend"
        assert name in plan.note


def test_a_regular_weekday_is_still_regular(cfg):
    plan = plan_session(cfg, datetime(2026, 11, 23, 10, 0))
    assert (plan.is_trading_day, plan.status) == (True, "regular")
