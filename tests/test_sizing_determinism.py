"""Sizing is a pure function. These tests assert that property directly,
because "the model does not choose the size" is the claim the whole submission
rests on, and a claim about structure should be tested structurally.
"""

import random

import pytest

from engine.errors import RiskViolation
from engine.sizing import size_position

from conftest import MEASURED


def test_identical_inputs_give_identical_output(cfg):
    px, atr = MEASURED["US.SPY"]
    runs = [size_position(cfg, symbol="US.SPY", price=px, atr20=atr, equity=100_000.0)
            for _ in range(50)]
    assert len({r.qty for r in runs}) == 1
    assert all(r == runs[0] for r in runs)


def test_sizing_source_is_always_deterministic(cfg):
    """Present in the ledger as a positive assertion. An absent field proves
    nothing; a field that always reads 'deterministic' can be checked."""
    for sym, (px, atr) in MEASURED.items():
        r = size_position(cfg, symbol=sym, price=px, atr20=atr, equity=100_000.0)
        assert r.sizing_source == "deterministic"


def test_measured_sizes_match_hand_calculation(cfg):
    """Guards against a refactor silently changing the arithmetic. Values
    computed independently from the 2026-11-19 measurements."""
    expected = {
        "US.SPY": (20, "notional_cap"),
        "US.QQQ": (23, "notional_cap"),
        "US.IWM": (56, "notional_cap"),
        "US.XLF": (269, "notional_cap"),
        "US.XLE": (245, "notional_cap"),
    }
    for sym, (qty, binding) in expected.items():
        px, atr = MEASURED[sym]
        r = size_position(cfg, symbol=sym, price=px, atr20=atr, equity=100_000.0)
        assert (r.qty, r.binding_constraint) == (qty, binding), sym


def test_realised_risk_stays_far_below_the_stated_ceiling(cfg):
    """An honest finding, pinned by a test. risk_per_trade_pct is 0.35 but the
    notional cap binds first on every symbol, so realised risk is 0.04-0.08%.
    The config says so; this asserts the config is telling the truth."""
    for sym, (px, atr) in MEASURED.items():
        r = size_position(cfg, symbol=sym, price=px, atr20=atr, equity=100_000.0)
        assert r.risk_pct_of_equity < 0.10, sym
        assert r.binding_constraint == "notional_cap", sym


def test_quiet_tape_is_refused_per_symbol(cfg):
    """The quietest ATR20 actually observed for XLF and XLE sat below their
    cost gates. Those trades would have been refused."""
    for sym, atr_min in (("US.XLF", 0.0352), ("US.XLE", 0.0586)):
        px, _ = MEASURED[sym]
        with pytest.raises(RiskViolation) as exc:
            size_position(cfg, symbol=sym, price=px, atr20=atr_min, equity=100_000.0)
        assert exc.value.reason_code == "ATR_BELOW_COST_GATE"


def test_symbol_outside_universe_is_refused(cfg):
    with pytest.raises(RiskViolation) as exc:
        size_position(cfg, symbol="US.TSLA", price=400.0, atr20=5.0, equity=100_000.0)
    assert exc.value.reason_code == "SYMBOL_NOT_IN_UNIVERSE"


@pytest.mark.parametrize("bad", [
    {"price": 0.0}, {"price": -10.0}, {"atr20": 0.0}, {"atr20": -1.0},
    {"equity": 0.0}, {"equity": -5000.0},
])
def test_nonsense_inputs_raise_rather_than_returning_a_size(cfg, bad):
    px, atr = MEASURED["US.SPY"]
    kwargs = {"symbol": "US.SPY", "price": px, "atr20": atr, "equity": 100_000.0}
    kwargs.update(bad)
    with pytest.raises(RiskViolation):
        size_position(cfg, **kwargs)


def test_tiny_account_is_refused_rather_than_sized_to_one_share(cfg):
    """At small equity the notional floor bites. One share of a $717
    instrument costs 0.28% round trip in commission alone -- more than the
    entire expected edge. Refusing is correct; a one-share trade is not."""
    px, atr = MEASURED["US.SPY"]
    with pytest.raises(RiskViolation) as exc:
        size_position(cfg, symbol="US.SPY", price=px, atr20=atr, equity=5_000.0)
    assert exc.value.reason_code in {"NOTIONAL_BELOW_FLOOR", "SIZE_BELOW_MINIMUM"}


def test_risk_never_exceeds_budget_across_randomised_inputs(cfg):
    """The property, not the branches: whatever the inputs, a sized position
    never risks more than risk_per_trade_pct of equity."""
    rng = random.Random(1120)
    limit = float(cfg.section("sizing")["risk_per_trade_pct"])
    sized = 0

    for _ in range(3000):
        sym = rng.choice(list(MEASURED))
        px, base = MEASURED[sym]
        try:
            r = size_position(
                cfg, symbol=sym,
                price=px * rng.uniform(0.5, 2.0),
                atr20=base * rng.uniform(0.3, 5.0),
                equity=rng.uniform(10_000.0, 1_000_000.0),
            )
        except RiskViolation:
            continue
        assert r.risk_pct_of_equity <= limit + 1e-9, (
            f"{sym} risked {r.risk_pct_of_equity:.4f}% > {limit}%"
        )
        assert r.qty >= 1
        assert r.cost_to_edge_ratio <= 1.0 / float(
            cfg.section("fees")["min_expected_edge_to_cost_ratio"]
        ) + 1e-9
        sized += 1

    assert sized > 1000, f"only {sized} valid sizes produced; sample too thin"
