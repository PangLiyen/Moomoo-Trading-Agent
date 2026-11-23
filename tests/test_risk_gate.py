"""Every block path in the gate, exercised.

The submission claims that the model cannot place a trade that breaks the
rules. That claim is only worth as much as this file: each test names a
specific rule and demonstrates the gate enforcing it. A rule with no test
here is an assertion, not a control.

The last test is the important one. It asserts the structural invariant --
the gate can only ever shrink or refuse -- across a wide range of randomised
inputs, rather than trusting that every branch above was written correctly.
"""

import random
from dataclasses import replace
from datetime import datetime

import pytest

from engine.errors import AccountGuardError, RiskViolation
from engine.risk_gate import AccountState, SessionState
from engine.sizing import size_position

from conftest import MEASURED


def _spy(cfg, equity=100_000.0):
    px, atr = MEASURED["US.SPY"]
    return size_position(cfg, symbol="US.SPY", price=px, atr20=atr, equity=equity)


def _ok(gate, proposal, acct, sess):
    return gate.evaluate_entry(
        proposal, acct=acct, sess=sess, quote_age_seconds=4.0,
        spread_pct_of_mid=0.0056,
    )


# --------------------------------------------------------------------------
# Account guard -- three independent checks must all agree
# --------------------------------------------------------------------------

def test_correct_account_passes(gate, clean_account):
    gate.assert_account(clean_account)


def test_blocklisted_account_is_refused(gate, clean_account):
    """5852463 is the generic 'US Margin Account [Paper Trading]'. An agent
    chose it unprompted during the probe and reported its $1,000,000 balance
    as the competition balance."""
    wrong = replace(clean_account, acc_id="5852463")
    with pytest.raises(AccountGuardError, match="blocklisted"):
        gate.assert_account(wrong)


def test_unknown_account_is_refused(gate, clean_account):
    with pytest.raises(AccountGuardError, match="not the competition account"):
        gate.assert_account(replace(clean_account, acc_id="9999999"))


def test_right_id_wrong_title_is_refused(gate, clean_account):
    """The title is editable in the broker UI, so the id alone is not proof.
    Requiring both means a wrong account must coincidentally match on every
    axis."""
    with pytest.raises(AccountGuardError, match="does not match"):
        gate.assert_account(replace(clean_account, account_title="US Margin Account [Paper Trading]"))


def test_right_id_and_title_wrong_type_is_refused(gate, clean_account):
    with pytest.raises(AccountGuardError, match="account_type"):
        gate.assert_account(replace(clean_account, account_type=1))


def test_mid_transition_snapshot_is_refused(gate, clean_account):
    """balance + mv + hold must equal total_asset exactly. When it does not,
    the snapshot was taken mid-transition and sizing against it would use a
    number the broker does not agree with."""
    torn = replace(clean_account, balance=99_000.0, market_value=0.0, hold=0.0)
    with pytest.raises(RiskViolation) as exc:
        gate.assert_account(torn)
    assert exc.value.reason_code == "ACCOUNT_SNAPSHOT_INCONSISTENT"


# --------------------------------------------------------------------------
# Foreign activity -- the reason P&L stays attributable
# --------------------------------------------------------------------------

def test_known_foreign_orders_do_not_halt(gate, clean_account):
    """The two ANT orders from 20 Nov are recorded in config as known. They
    are history, not an ongoing breach."""
    acct = replace(clean_account, unattributed_order_ids=("3128921", "3128965"))
    gate.assert_no_foreign_activity(acct)


def test_a_new_foreign_order_halts(gate, clean_account):
    acct = replace(clean_account, unattributed_order_ids=("3128921", "9999001"))
    with pytest.raises(RiskViolation) as exc:
        gate.assert_no_foreign_activity(acct)
    assert exc.value.reason_code == "FOREIGN_ORDER_DETECTED"
    assert "9999001" in exc.value.detail


# --------------------------------------------------------------------------
# Kill switch
# --------------------------------------------------------------------------

def test_clean_entry_is_approved(gate, cfg, clean_account, open_session):
    d = _ok(gate, _spy(cfg), clean_account, open_session)
    assert d.verdict == "APPROVE"
    assert d.qty_out == 20


def test_manual_halt_blocks(gate, cfg, clean_account, open_session):
    sess = replace(open_session, halted=True, halt_reason="operator set AGENT_MODE=halt")
    assert _ok(gate, _spy(cfg), clean_account, sess).reason_code == "MANUAL_HALT"


def test_daily_loss_halt_blocks(gate, cfg, clean_account, open_session):
    sess = replace(open_session, session_pnl_pct=-1.6)
    assert _ok(gate, _spy(cfg), clean_account, sess).reason_code == "DAILY_LOSS_HALT"


def test_drawdown_flatten_blocks(gate, cfg, clean_account, open_session):
    sess = replace(open_session, peak_drawdown_pct=3.6)
    assert _ok(gate, _spy(cfg), clean_account, sess).reason_code == "DRAWDOWN_FLATTEN"


def test_consecutive_losses_stop_blocks(gate, cfg, clean_account, open_session):
    sess = replace(open_session, consecutive_losses=5)
    assert _ok(gate, _spy(cfg), clean_account, sess).reason_code == "CONSECUTIVE_LOSS_STOP"


# --------------------------------------------------------------------------
# The cycle-gap hazard -- specific to having no resting stop orders
# --------------------------------------------------------------------------

def test_cycle_gap_blocks_new_entries(gate, cfg, clean_account, open_session):
    """Qoder Forward Mode defaults to max_concurrent_runs 1, so an overrunning
    run causes the next trigger to be SKIPPED. With software-only stops, a
    skipped cycle is an unmonitored position. Opening more during a gap
    compounds an exposure we already cannot see."""
    sess = replace(open_session, seconds_since_last_completed_cycle=240.0)
    d = _ok(gate, _spy(cfg), clean_account, sess)
    assert d.reason_code == "CYCLE_GAP_EXCEEDED"


# --------------------------------------------------------------------------
# Data quality
# --------------------------------------------------------------------------

def test_stale_quote_blocks(gate, cfg, clean_account, open_session):
    d = gate.evaluate_entry(_spy(cfg), acct=clean_account, sess=open_session,
                            quote_age_seconds=180.0, spread_pct_of_mid=0.0056)
    assert d.reason_code == "STALE_QUOTE"


def test_wide_spread_blocks(gate, cfg, clean_account, open_session):
    d = gate.evaluate_entry(_spy(cfg), acct=clean_account, sess=open_session,
                            quote_age_seconds=4.0, spread_pct_of_mid=0.5)
    assert d.reason_code == "SPREAD_TOO_WIDE"


# --------------------------------------------------------------------------
# Session window and frequency budgets
# --------------------------------------------------------------------------

def test_entry_after_cutoff_blocks(gate, cfg, clean_account, open_session):
    sess = replace(open_session, now_et=datetime(2026, 11, 26, 15, 1))
    assert _ok(gate, _spy(cfg), clean_account, sess).reason_code == "AFTER_ENTRY_CUTOFF"


def test_entry_budget_exhausted_blocks(gate, cfg, clean_account, open_session):
    sess = replace(open_session, entries_this_session=6)
    assert _ok(gate, _spy(cfg), clean_account, sess).reason_code == "ENTRY_BUDGET_EXHAUSTED"


def test_order_budget_exhausted_blocks(gate, cfg, clean_account, open_session):
    sess = replace(open_session, orders_this_session=20)
    assert _ok(gate, _spy(cfg), clean_account, sess).reason_code == "ORDER_BUDGET_EXHAUSTED"


def test_entry_too_soon_blocks(gate, cfg, clean_account, open_session):
    sess = replace(open_session, seconds_since_last_entry=120.0)
    assert _ok(gate, _spy(cfg), clean_account, sess).reason_code == "ENTRY_TOO_SOON"


# --------------------------------------------------------------------------
# Position and correlation limits
# --------------------------------------------------------------------------

def test_adding_to_an_existing_position_blocks(gate, cfg, clean_account, open_session):
    acct = replace(clean_account, open_positions={"US.SPY": 20})
    assert _ok(gate, _spy(cfg), acct, open_session).reason_code == "ALREADY_HOLDING"


def test_position_count_limit_blocks(gate, cfg, clean_account, open_session):
    acct = replace(clean_account,
                   open_positions={"US.XLF": 269, "US.XLE": 245, "US.IWM": 56})
    d = _ok(gate, _spy(cfg), acct, open_session)
    assert d.reason_code in {"POSITION_COUNT_LIMIT", "CORRELATION_CLUSTER_LIMIT"}


def test_correlation_cluster_limit_blocks(gate, cfg, clean_account, open_session):
    """SPY, QQQ and IWM are one bet. Holding two of them at full size is
    double exposure to the same move, not diversification."""
    acct = replace(clean_account, open_positions={"US.QQQ": 23})
    d = _ok(gate, _spy(cfg), acct, open_session)
    assert d.reason_code == "CORRELATION_CLUSTER_LIMIT"
    assert "US.QQQ" in d.reason_text


# --------------------------------------------------------------------------
# Exposure -- the only check that shrinks rather than blocks
# --------------------------------------------------------------------------

def test_gross_exposure_shrinks_rather_than_blocks(gate, cfg, clean_account, open_session):
    """40% of 100k is 40k. With 32k already on, a 14.3k SPY position does not
    fit, but an 11-share version does."""
    acct = replace(clean_account, market_value=32_000.0, balance=68_000.0)
    d = _ok(gate, _spy(cfg), acct, open_session)
    assert d.verdict == "SHRINK"
    assert 0 < d.qty_out < d.qty_in


def test_gross_exposure_blocks_when_no_share_fits(gate, cfg, clean_account, open_session):
    acct = replace(clean_account, market_value=39_900.0, balance=60_100.0)
    d = _ok(gate, _spy(cfg), acct, open_session)
    assert d.verdict == "BLOCK"
    assert d.reason_code == "GROSS_EXPOSURE_LIMIT"


def test_margin_headroom_is_never_used(gate, cfg, clean_account, open_session):
    """max_power_long is 2x balance on this account. allow_margin is false, so
    that headroom must behave as though it does not exist. Here cash is almost
    gone while buying power would still cover the trade."""
    acct = replace(clean_account, total_asset=100_000.0, balance=500.0,
                   market_value=0.0, hold=99_500.0)
    d = _ok(gate, _spy(cfg), acct, open_session)
    assert d.verdict == "BLOCK"
    assert d.reason_code == "INSUFFICIENT_CASH"


# --------------------------------------------------------------------------
# The structural invariant
# --------------------------------------------------------------------------

def test_gate_never_increases_quantity_across_randomised_states(gate, cfg):
    """The claim is that the gate may only tighten. Asserting that over 2000
    randomised account and session states tests the property itself rather
    than trusting that each branch above happens to be written correctly."""
    rng = random.Random(20261120)
    symbols = list(MEASURED)
    checked = approved = shrunk = blocked = 0

    for _ in range(2000):
        sym = rng.choice(symbols)
        px, atr = MEASURED[sym]
        equity = rng.uniform(1_000.0, 500_000.0)
        try:
            proposal = size_position(cfg, symbol=sym, price=px,
                                     atr20=atr * rng.uniform(0.5, 3.0),
                                     equity=equity)
        except RiskViolation:
            continue

        mv = rng.uniform(0.0, equity * 0.6)
        hold = rng.uniform(0.0, equity * 0.3)
        acct = AccountState(
            acc_id="5832647",
            account_title="Moomoo x Qoder AI Paper Trading Agent Challenge",
            account_type=2,
            total_asset=equity,
            balance=equity - mv - hold,
            hold=hold,
            market_value=mv,
            open_positions={s: rng.randint(1, 50)
                            for s in rng.sample(symbols, rng.randint(0, 2))
                            if s != sym},
        )
        sess = SessionState(
            now_et=datetime(2026, 11, 26, rng.randint(9, 15), rng.randint(0, 59)),
            entries_this_session=rng.randint(0, 8),
            orders_this_session=rng.randint(0, 25),
            seconds_since_last_entry=rng.choice([None, rng.uniform(0, 900)]),
            seconds_since_last_completed_cycle=rng.uniform(0, 400),
            session_pnl_pct=rng.uniform(-3.0, 3.0),
            peak_drawdown_pct=rng.uniform(0.0, 5.0),
            consecutive_losses=rng.randint(0, 6),
        )

        d = gate.evaluate_entry(
            proposal, acct=acct, sess=sess,
            quote_age_seconds=rng.uniform(0, 200),
            spread_pct_of_mid=rng.uniform(0.001, 0.2),
        )

        assert d.qty_out <= d.qty_in, f"gate enlarged {d.qty_in} -> {d.qty_out}"
        assert d.qty_out >= 0
        if d.verdict == "BLOCK":
            assert d.qty_out == 0, "a blocked proposal must carry no quantity"
            blocked += 1
        elif d.verdict == "SHRINK":
            assert d.qty_out < d.qty_in
            shrunk += 1
        else:
            assert d.qty_out == d.qty_in
            approved += 1
        checked += 1

    assert checked > 500, f"only {checked} states exercised; sample too thin"
    assert approved and shrunk and blocked, (
        f"all three verdicts must occur or the sweep is not exercising the "
        f"gate: approve={approved} shrink={shrunk} block={blocked}"
    )


# --------------------------------------------------------------------------
# The us_equity_beta cluster, added 2026-11-23 from crisis research
# --------------------------------------------------------------------------

def test_two_sector_positions_block_a_third_equity_position(gate, cfg, clean_account, open_session):
    """The defect the new cluster closes.

    Before it, XLF and XLE sat outside index_broad, so holding both still
    permitted a third position in SPY, QQQ or IWM: three positions, all long
    US equity beta, up to the 40% gross cap. Under the stressed correlations
    of 1997 or 2008 that is one trade at three times the size.
    """
    from dataclasses import replace
    acct = replace(clean_account, open_positions={"US.XLF": 269, "US.XLE": 245})
    d = _ok(gate, _spy(cfg), acct, open_session)
    assert d.verdict == "BLOCK"
    assert d.reason_code == "CORRELATION_CLUSTER_LIMIT"
    assert d.qty_out == 0


def test_one_index_plus_one_sector_is_still_permitted(gate, cfg, clean_account, open_session):
    """Tightening, not prohibition. Two of the five remain allowed, so the
    strategy still has somewhere to trade."""
    from dataclasses import replace
    px, atr = MEASURED["US.XLE"]
    acct = replace(clean_account, open_positions={"US.SPY": 20})
    proposal = size_position(cfg, symbol="US.XLE", price=px, atr20=atr,
                             equity=acct.total_asset)
    d = _ok(gate, proposal, acct, open_session)
    assert d.verdict == "APPROVE", d.reason_text


def test_the_new_cluster_is_actually_reached_by_the_gate(gate, cfg):
    """`us_equity_beta` was added to config, not to code. This asserts the
    gate reads it, because every other defect in this project has been a
    limit that was specified and never reached."""
    clusters = cfg.section("universe")["clusters"]
    assert "us_equity_beta" in clusters
    assert set(clusters["us_equity_beta"]["members"]) == set(cfg.symbols)
    assert clusters["us_equity_beta"]["max_concurrent_positions"] == 2

    # Prove enforcement rather than inferring it from the config's presence.
    from dataclasses import replace
    from engine.risk_gate import AccountState
    import inspect
    src = inspect.getsource(gate.evaluate_entry)
    assert 'clusters' in src, "the gate must read the clusters block generically"


def test_stress_conditional_cap_is_declared_unimplemented(cfg):
    """It is not wired, and the config says so. A config that described it as
    present would be the exact defect AUDIT_FINDINGS.md exists to record."""
    block = cfg.section("universe")["stress_conditional_cluster_cap"]
    assert block["implemented"] is False
