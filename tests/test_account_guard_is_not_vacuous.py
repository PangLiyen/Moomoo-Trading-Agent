"""Regression tests for a real defect found on 2026-11-20.

The cycle driver built its AccountState from `risk_limits.yaml` and then asked
the account guard to validate it against `risk_limits.yaml`. Every check
passed, always, and could only have failed if the YAML contradicted itself.
The guard that exists because an agent once traded the wrong account was, on
the live path, checking nothing.

The same defect starved two other controls: `open_positions` defaulted to
empty, so the correlation-cluster rule could not fire while we actually held
1 SPY; and `unattributed_order_ids` defaulted to empty, so the foreign-writer
halt could never trigger.

The lesson these tests encode: a control is only as good as the inputs the
caller supplies, and gate-level tests that pass fixtures directly will not
catch a caller that supplies nothing. Hence tests at the boundary.
"""

import pytest

from engine.account import account_state_from_api, describe_guard_inputs, ledger_order_ids
from engine.errors import AccountGuardError, RiskViolation

GOOD = {
    "acc_id": "5832647",
    "account_title": "Moomoo x Qoder AI Paper Trading Agent Challenge",
    "account_type": 2,
    "total_asset": 99992.80,
    "balance": 99285.34,
    "hold": 0.0,
    "market_value": 707.46,
}


def _build(payload=None, **kw):
    kw.setdefault("positions", {})
    kw.setdefault("account_order_ids", [])
    kw.setdefault("ledger_order_ids", [])
    return account_state_from_api(payload or GOOD, **kw)


# --- the fields must come from the broker, and all of them ---

@pytest.mark.parametrize("field", list(GOOD))
def test_every_account_field_is_mandatory(field):
    """None may be defaulted. A defaulted field is a claim about the account
    that no one checked."""
    payload = {k: v for k, v in GOOD.items() if k != field}
    with pytest.raises(AccountGuardError, match="missing"):
        _build(payload)


def test_missing_field_error_explains_the_tautology():
    """The message has to say why, or the next person 'helpfully' fills the
    field from config and restores the bug."""
    payload = {k: v for k, v in GOOD.items() if k != "acc_id"}
    with pytest.raises(AccountGuardError, match="compare config against itself"):
        _build(payload)


# --- the guard must actually be capable of failing ---

def test_guard_rejects_a_broker_response_for_the_wrong_account(gate):
    """The check the old driver could not perform: broker says 5852463, config
    says 5832647."""
    wrong = {**GOOD, "acc_id": "5852463",
             "account_title": "US Margin Account [Paper Trading]",
             "account_type": 1}
    with pytest.raises(AccountGuardError):
        gate.assert_account(_build(wrong))


def test_guard_passes_only_on_genuine_agreement(gate):
    gate.assert_account(_build())


def test_guard_inputs_record_both_sides(cfg):
    """Recording only 'guard passed' leaves no way to tell a real comparison
    from a vacuous one. Both sides go in the ledger."""
    desc = describe_guard_inputs(_build(), cfg)
    assert desc["from_api"]["acc_id"] == "5832647"
    assert desc["from_config"]["acc_id"] == cfg.acc_id
    assert set(desc) >= {"from_api", "from_config", "open_positions",
                         "unattributed_order_ids", "reconciles"}


# --- positions must be real, and must reach the gate ---

def test_positions_are_carried_through_not_defaulted():
    acct = _build(positions={"US.SPY": 1})
    assert acct.open_positions == {"US.SPY": 1}


def test_zero_quantity_position_stubs_are_dropped():
    """moomoo leaves zero-qty stub rows after a close. Treating one as a held
    position would block re-entry in that symbol for the rest of the day."""
    acct = _build(positions={"US.SPY": 0, "US.ANT": 0, "US.QQQ": 3})
    assert acct.open_positions == {"US.QQQ": 3}


def test_the_actual_20_nov_state_blocks_the_cluster(gate, cfg):
    """The defect's live consequence, pinned.

    Probe order 3128955 filled at 10:25:18 ET, leaving 1 SPY held. With
    positions defaulted to empty the gate approved cluster members; with the
    real state it blocks them.
    """
    from engine.sizing import size_position
    from engine.risk_gate import SessionState
    from datetime import datetime

    acct = _build(positions={"US.SPY": 1})
    sess = SessionState(
        now_et=datetime(2026, 11, 20, 11, 0), entries_this_session=0,
        orders_this_session=0, seconds_since_last_entry=None,
        seconds_since_last_completed_cycle=10.0, session_pnl_pct=0.0,
        peak_drawdown_pct=0.0, consecutive_losses=0,
    )
    proposal = size_position(cfg, symbol="US.QQQ", price=629.54,
                             atr20=2.9326, equity=acct.total_asset)
    decision = gate.evaluate_entry(proposal, acct=acct, sess=sess,
                                   quote_age_seconds=3.3,
                                   spread_pct_of_mid=0.0048)
    assert decision.verdict == "BLOCK"
    assert decision.reason_code == "CORRELATION_CLUSTER_LIMIT"
    assert decision.qty_out == 0


# --- foreign orders must be derived, not supplied as an empty default ---

def test_unattributed_orders_are_the_set_difference():
    acct = _build(account_order_ids=["3128921", "3128953", "3128965"],
                  ledger_order_ids=["3128953"])
    assert acct.unattributed_order_ids == ("3128921", "3128965")


def test_known_foreign_ids_pass_and_a_novel_one_halts(gate):
    """The two ANT orders are recorded in config as known history. Anything
    else means a second writer is active now."""
    gate.assert_no_foreign_activity(
        _build(account_order_ids=["3128921", "3128965"], ledger_order_ids=[])
    )
    with pytest.raises(RiskViolation) as exc:
        gate.assert_no_foreign_activity(
            _build(account_order_ids=["3128921", "9999001"], ledger_order_ids=[])
        )
    assert exc.value.reason_code == "FOREIGN_ORDER_DETECTED"


def test_ledger_order_ids_reads_several_record_shapes():
    """Order ids appear under different keys depending on the stage that wrote
    the record. Missing one would make our own order look foreign and halt
    the engine on its own activity."""
    records = [
        {"stage": "order", "order_id": "3128953"},
        {"stage": "fill", "broker_order_id": "3128956"},
        {"stage": "order", "order": {"order_id": "3129023"}},
        {"stage": "signal", "verdict": "NO_SETUP"},
    ]
    assert ledger_order_ids(records) == {"3128953", "3128956", "3129023"}


def test_non_numeric_account_figure_is_refused():
    with pytest.raises(AccountGuardError, match="non-numeric"):
        _build({**GOOD, "balance": "n/a"})
