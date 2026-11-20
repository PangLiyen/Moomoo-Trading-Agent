"""The config loader refuses to start on an incomplete or incoherent config.

The principle under test: absence is not permission. An unset `allow_shorting`
must not mean shorting is allowed; it must mean the engine will not run. The
alternative -- a default -- is a number nobody committed to and nobody
reviewed.
"""

import copy

import pytest
import yaml

from engine.config import RiskConfig
from engine.errors import ConfigError


@pytest.fixture
def raw(cfg):
    return copy.deepcopy(cfg.raw)


def _build(raw, tmp_path, name="r.yaml"):
    p = tmp_path / name
    p.write_text(yaml.safe_dump(raw), encoding="utf-8")
    return RiskConfig.load(p)


def test_real_config_loads(cfg):
    assert cfg.acc_id == "5832647"
    assert cfg.starting_equity == 100_000.0
    assert len(cfg.sha256) == 64


def test_empty_account_id_is_refused(raw, tmp_path):
    raw["account"]["competition_acc_id"] = ""
    with pytest.raises(ConfigError, match="fail-closed"):
        _build(raw, tmp_path)


def test_live_environment_is_refused(raw, tmp_path):
    """Pointing this engine at a live account must not be a one-word edit."""
    raw["account"]["environment"] = "live"
    with pytest.raises(ConfigError, match="never been validated"):
        _build(raw, tmp_path)


def test_null_starting_equity_is_refused(raw, tmp_path):
    raw["account"]["starting_equity_usd"] = None
    with pytest.raises(ConfigError, match="every position size"):
        _build(raw, tmp_path)


def test_disabling_the_name_check_is_refused(raw, tmp_path):
    raw["account"]["require_name_match"] = False
    with pytest.raises(ConfigError, match="plausible-looking wrong account"):
        _build(raw, tmp_path)


def test_account_in_its_own_blocklist_is_refused(raw, tmp_path):
    raw["account"]["blocklist_acc_ids"].append("5832647")
    with pytest.raises(ConfigError, match="blocklist"):
        _build(raw, tmp_path)


@pytest.mark.parametrize("path", [
    ("universe", "instrument_rules", "allow_shorting"),
    ("universe", "instrument_rules", "allow_margin"),
    ("universe", "instrument_rules", "allow_options"),
    ("universe", "allow_symbols_outside_list"),
    ("sizing", "model_may_choose_quantity"),
])
def test_prohibition_set_true_is_refused(raw, tmp_path, path):
    node = raw
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = True
    with pytest.raises(ConfigError, match="must be exactly false"):
        _build(raw, tmp_path)


@pytest.mark.parametrize("path", [
    ("universe", "instrument_rules", "allow_shorting"),
    ("sizing", "model_may_choose_quantity"),
])
def test_prohibition_merely_absent_is_refused(raw, tmp_path, path):
    """The core of fail-closed: a deleted line must not read as permission."""
    node = raw
    for key in path[:-1]:
        node = node[key]
    del node[path[-1]]
    with pytest.raises(ConfigError, match="absence is not permission"):
        _build(raw, tmp_path)


def test_model_may_choose_quantity_cannot_be_enabled(raw, tmp_path):
    """The single most load-bearing flag in the file. If a model can pick the
    size, nothing else in this config is a real constraint."""
    raw["sizing"]["model_may_choose_quantity"] = True
    with pytest.raises(ConfigError):
        _build(raw, tmp_path)


def test_null_cost_gate_is_refused(raw, tmp_path):
    raw["fees"]["min_atr20_usd"] = None
    with pytest.raises(ConfigError, match="clear its own costs"):
        _build(raw, tmp_path)


def test_scalar_cost_gate_is_refused(raw, tmp_path):
    """One threshold cannot be right for both a $717 instrument and a $56 one."""
    raw["fees"]["min_atr20_usd"] = 0.5
    with pytest.raises(ConfigError, match="per-symbol mapping"):
        _build(raw, tmp_path)


def test_symbol_without_a_cost_gate_is_refused(raw, tmp_path):
    raw["universe"]["symbols"].append("US.DIA")
    with pytest.raises(ConfigError, match="no gate for"):
        _build(raw, tmp_path)


def test_position_cap_above_gross_cap_is_refused(raw, tmp_path):
    """Individually valid numbers describing an impossible policy: a single
    position could not open without breaching the gross limit."""
    raw["sizing"]["max_position_notional_pct"] = 50.0
    raw["sizing"]["max_gross_exposure_pct"] = 40.0
    with pytest.raises(ConfigError, match="exceeds"):
        _build(raw, tmp_path)


def test_inverted_kill_switch_thresholds_are_refused(raw, tmp_path):
    """The soft limit must trip before the hard one, or the hard one is dead."""
    raw["kill_switch"]["daily_loss_halt_pct"] = 4.0
    raw["kill_switch"]["peak_drawdown_flatten_pct"] = 3.5
    with pytest.raises(ConfigError, match="must be below"):
        _build(raw, tmp_path)


def test_sizing_distance_below_stop_trigger_is_refused(raw, tmp_path):
    """Sizing off the trigger rather than trigger-plus-buffer would assume the
    software stop fills better than its own trigger, understating risk."""
    raw["sizing"]["sizing_risk_distance_atr_mult"] = 0.8
    raw["sizing"]["stop_distance_atr_mult"] = 1.0
    with pytest.raises(ConfigError, match="understates risk"):
        _build(raw, tmp_path)


def test_rules_hash_changes_when_any_limit_changes(raw, tmp_path):
    """The ledger pins this hash, so a config edit is visible in the chain
    rather than only in git history."""
    a = _build(raw, tmp_path, "a.yaml")
    raw["sizing"]["risk_per_trade_pct"] = 0.36
    b = _build(raw, tmp_path, "b.yaml")
    assert a.sha256 != b.sha256
