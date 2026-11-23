"""Tests for state reconstruction, and therefore for the kill switch.

The driver previously passed literal zeros for session P&L, drawdown,
consecutive losses and entry spacing, so five risk-gate branches were
unreachable. These tests build ledgers and assert the branches now fire,
because asserting that the state function returns a number proves nothing
about whether the gate ever sees it.
"""

from dataclasses import replace
from datetime import datetime, timedelta

import pytest

from engine.risk_gate import AccountState
from engine.session_state import reconstruct
from engine.sizing import size_position

from conftest import MEASURED

NOW = datetime(2026, 11, 23, 13, 0)


def _entry(oid, symbol, qty, price, atr, et, cycle="C20261123T1030"):
    return [
        {"stage": "execute", "order_id": oid, "symbol": symbol, "side": "BUY",
         "entry_atr20": atr, "cycle_id": cycle, "ts_utc": f"{et}Z"},
        {"stage": "fill", "order_id": oid, "symbol": symbol, "side": "BUY",
         "verdict": "FILLED", "filled_qty": qty, "avg_fill_price": price,
         "fill_time_et": et, "ts_utc": f"{et}Z"},
    ]


def _exit(symbol, pnl, et, rule="STOP_HIT"):
    return [{"stage": "exit", "symbol": symbol, "exit_rule": rule,
             "reason_code": rule, "realised_pnl": pnl, "exit_time_et": et,
             "ts_utc": f"{et}Z"}]


def _state(cfg, records, *, equity, positions, now=NOW, **kw):
    return reconstruct(records, cfg, now_et=now, broker_equity=equity,
                       broker_positions=positions, **kw)


# --------------------------------------------------------------------------
# Position reconstruction
# --------------------------------------------------------------------------

def test_an_open_position_is_reconstructed_with_its_stop_inputs(cfg):
    recs = _entry("1", "US.SPY", 20, 712.50, 2.10, "2026-11-23T10:30:07")
    st = _state(cfg, recs, equity=99_850.0, positions={"US.SPY": 20})
    assert st.open_quantities() == {"US.SPY": 20}
    pos = st.positions[0]
    assert (pos.entry_price, pos.entry_atr20) == (712.50, 2.10)
    assert st.warnings == ()


def test_a_closed_round_trip_leaves_no_position(cfg):
    recs = _entry("1", "US.SPY", 20, 712.50, 2.10, "2026-11-23T10:30:07")
    recs += [{"stage": "fill", "order_id": "2", "symbol": "US.SPY",
              "side": "SELL", "verdict": "FILLED", "filled_qty": 20,
              "avg_fill_price": 710.0, "fill_time_et": "2026-11-23T11:00:00",
              "ts_utc": "2026-11-23T11:00:00Z"}]
    st = _state(cfg, recs, equity=99_800.0, positions={})
    assert st.open_quantities() == {}


def test_a_broker_position_the_ledger_cannot_explain_is_surfaced(cfg):
    """The ledger says what we did; the broker says what is. The
    disagreement is the finding, not something to resolve silently."""
    st = _state(cfg, [], equity=100_000.0, positions={"US.SPY": 20})
    assert any("position mismatch" in w for w in st.warnings)


def test_a_fill_without_an_entry_atr_is_refused_not_guessed(cfg):
    """Without the entry ATR there is no stop level, and inventing one would
    produce a position the engine believes it is protecting."""
    recs = [{"stage": "fill", "order_id": "9", "symbol": "US.SPY",
             "side": "BUY", "verdict": "FILLED", "filled_qty": 20,
             "avg_fill_price": 712.5, "fill_time_et": "2026-11-23T10:30:07",
             "ts_utc": "2026-11-23T10:30:07Z"}]
    st = _state(cfg, recs, equity=99_850.0, positions={"US.SPY": 20})
    assert st.positions == ()
    assert any("stop level unknown" in w or "ATR" in w for w in st.warnings)


# --------------------------------------------------------------------------
# The kill-switch inputs, and the gate branches they unlock
# --------------------------------------------------------------------------

def test_daily_loss_now_reaches_the_gate_and_blocks(cfg, gate, clean_account):
    """-1.6% against a 1.5% halt. This branch was unreachable before."""
    st = _state(cfg, [], equity=98_400.0, positions={})
    assert st.session_state.session_pnl_pct < -1.5

    px, atr = MEASURED["US.SPY"]
    proposal = size_position(cfg, symbol="US.SPY", price=px, atr20=atr,
                             equity=98_400.0)
    acct = replace(clean_account, total_asset=98_400.0, balance=98_400.0)
    d = gate.evaluate_entry(proposal, acct=acct, sess=st.session_state,
                            quote_age_seconds=4.0, spread_pct_of_mid=0.006)
    assert d.reason_code == "DAILY_LOSS_HALT"


def test_drawdown_is_measured_from_recorded_equity_peaks(cfg):
    """P2 is 'max drawdown below 0.6%'. Nothing recorded equity on any cycle
    record, so the prediction could not be evaluated at all."""
    recs = [{"stage": "account_snapshot", "equity": 101_000.0,
             "cycle_id": "C20261123T1030"}]
    st = _state(cfg, recs, equity=100_395.0, positions={})
    assert st.peak_equity == 101_000.0
    assert st.session_state.peak_drawdown_pct == pytest.approx(0.599, abs=0.01)


def test_consecutive_losses_accumulate_and_a_win_resets_them(cfg):
    losses = (_exit("US.SPY", -40.0, "2026-11-23T10:40:00")
              + _exit("US.XLF", -32.0, "2026-11-23T11:10:00")
              + _exit("US.XLE", -28.0, "2026-11-23T11:50:00"))
    assert _state(cfg, losses, equity=99_900.0,
                  positions={}).session_state.consecutive_losses == 3

    with_win = losses + _exit("US.IWM", +55.0, "2026-11-23T12:10:00", "TARGET_HIT")
    assert _state(cfg, with_win, equity=99_955.0,
                  positions={}).session_state.consecutive_losses == 0


def test_a_losing_streak_does_not_reset_at_midnight(cfg):
    """Five consecutive losses stop the session. A streak that resets with the
    calendar is not a streak."""
    recs = (_exit("US.SPY", -40.0, "2026-11-20T15:00:00")
            + _exit("US.QQQ", -31.0, "2026-11-23T10:40:00"))
    assert _state(cfg, recs, equity=99_900.0,
                  positions={}).session_state.consecutive_losses == 2


def test_entry_spacing_now_reaches_the_gate_and_blocks(cfg, gate, clean_account):
    recs = _entry("1", "US.SPY", 20, 712.50, 2.10, "2026-11-23T12:58:00")
    st = _state(cfg, recs, equity=100_000.0, positions={"US.SPY": 20})
    assert st.session_state.seconds_since_last_entry == pytest.approx(120.0)

    px, atr = MEASURED["US.XLE"]
    proposal = size_position(cfg, symbol="US.XLE", price=px, atr20=atr,
                             equity=100_000.0)
    d = gate.evaluate_entry(proposal, acct=clean_account, sess=st.session_state,
                            quote_age_seconds=4.0, spread_pct_of_mid=0.006)
    assert d.reason_code == "ENTRY_TOO_SOON"


def test_cycle_gap_is_measured_from_the_last_recorded_cycle(cfg):
    """Forward Mode skips the next trigger when a run overruns. With
    software-only stops a skipped cycle is an unmonitored position."""
    recs = [{"stage": "cycle", "verdict": "NO_ACTION",
             "cycle_id": "C20261123T1250"}]
    st = _state(cfg, recs, equity=100_000.0, positions={})
    assert st.session_state.seconds_since_last_completed_cycle == pytest.approx(600.0)


def test_the_first_cycle_of_a_session_reports_no_gap(cfg):
    st = _state(cfg, [], equity=100_000.0, positions={})
    assert st.session_state.seconds_since_last_completed_cycle == 0.0


def test_entry_and_order_counts_are_scoped_to_today(cfg):
    recs = (_entry("1", "US.SPY", 20, 712.0, 2.1, "2026-11-20T10:30:00",
                   cycle="C20261120T1030")
            + _entry("2", "US.XLF", 269, 55.6, 0.09, "2026-11-23T10:35:00",
                     cycle="C20261123T1035"))
    st = _state(cfg, recs, equity=100_000.0, positions={"US.XLF": 269})
    assert st.session_state.entries_this_session == 1
    assert st.session_state.orders_this_session == 1


def test_an_explicit_halt_propagates(cfg):
    st = _state(cfg, [], equity=100_000.0, positions={},
                halted=True, halt_reason="AGENT_MODE=halt")
    assert st.session_state.halted is True
    assert "AGENT_MODE" in st.session_state.halt_reason


def test_state_is_derived_only_from_records_and_broker_figures(cfg):
    """No side file. A state file is a second source of truth that can
    disagree with the record, and when it disagrees the thing that decides is
    the file while the thing that gets audited is the ledger."""
    import inspect
    from engine import session_state
    src = inspect.getsource(session_state)
    for forbidden in ("open(", "json.load", "Path(", "read_text"):
        assert forbidden not in src, (
            f"session_state reads external state via {forbidden!r}; it must "
            f"derive everything from the ledger records passed to it"
        )
