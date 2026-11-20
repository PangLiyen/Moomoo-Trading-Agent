"""Fail-closed loader for risk_limits.yaml.

Two rules govern this module.

First: the config is read by code, never interpreted by a model. Nothing in
this file takes free text and decides what it means. Every value is read at a
known key, type-checked, and range-checked.

Second: absence is not permission. A missing limit raises rather than
defaulting, because a default is a number nobody committed to and nobody
reviewed. `allow_shorting` unset must not mean shorting is allowed; it must
mean the config is incomplete and the engine will not start.

The validation below is deliberately noisy about *why* something failed. An
engine that refuses to start at 09:28 ET is only useful if the operator can
fix it before the open.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from .errors import ConfigError
from .ledger import file_sha256

# Limits that must be present and explicitly false. Listing them here means a
# future edit that drops one is caught at startup rather than discovered by a
# short position appearing in the account.
_REQUIRED_FALSE = (
    ("universe", "instrument_rules", "allow_options"),
    ("universe", "instrument_rules", "allow_shorting"),
    ("universe", "instrument_rules", "allow_margin"),
    ("universe", "instrument_rules", "allow_leveraged_or_inverse_etfs"),
    ("universe", "allow_symbols_outside_list"),
    ("sizing", "model_may_choose_quantity"),
)

_REQUIRED_NUMERIC = (
    ("sizing", "risk_per_trade_pct"),
    ("sizing", "stop_distance_atr_mult"),
    ("sizing", "sizing_risk_distance_atr_mult"),
    ("sizing", "max_position_notional_pct"),
    ("sizing", "max_gross_exposure_pct"),
    ("sizing", "max_concurrent_positions"),
    ("fees", "commission_per_filled_order_usd"),
    ("fees", "min_expected_edge_to_cost_ratio"),
    ("frequency", "max_entries_per_session"),
    ("frequency", "max_orders_per_session"),
    ("kill_switch", "daily_loss_halt_pct"),
    ("kill_switch", "peak_drawdown_flatten_pct"),
    ("data_quality", "max_quote_age_seconds"),
    ("data_quality", "max_spread_pct_of_mid"),
)


def _dig(tree: dict[str, Any], path: tuple[str, ...]) -> Any:
    node: Any = tree
    for key in path:
        if not isinstance(node, dict) or key not in node:
            raise ConfigError(
                f"missing required key: {'.'.join(path)} "
                f"(absence is not permission — set it explicitly)"
            )
        node = node[key]
    return node


class RiskConfig:
    """Validated view over risk_limits.yaml.

    Holds the file's sha256 so the ledger can pin which exact rules were in
    force. A config change is therefore visible in the chain, not just in git.
    """

    def __init__(self, raw: dict[str, Any], *, path: Path, sha256: str):
        self.raw = raw
        self.path = path
        self.sha256 = sha256
        self._validate()

    # ---------- construction ----------

    @classmethod
    def load(cls, path: str | Path) -> "RiskConfig":
        p = Path(path)
        if not p.exists():
            raise ConfigError(f"config not found: {p}")
        try:
            raw = yaml.safe_load(p.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ConfigError(f"{p} is not valid YAML: {exc}") from exc
        if not isinstance(raw, dict):
            raise ConfigError(f"{p} did not parse to a mapping")
        return cls(raw, path=p, sha256=file_sha256(p))

    # ---------- validation ----------

    def _validate(self) -> None:
        self._validate_account()
        self._validate_prohibitions()
        self._validate_numerics()
        self._validate_universe_gates()
        self._validate_coherence()

    def _validate_account(self) -> None:
        acc = _dig(self.raw, ("account",))

        acc_id = acc.get("competition_acc_id")
        if not acc_id or not str(acc_id).strip():
            raise ConfigError(
                "account.competition_acc_id is empty — the account guard is "
                "fail-closed, so an unset account blocks every proposal"
            )

        env = acc.get("environment")
        if env != "paper":
            raise ConfigError(
                f"account.environment is {env!r}; only 'paper' is accepted. "
                "This engine has never been validated against a live account "
                "and must not be pointed at one by editing a single string."
            )

        equity = acc.get("starting_equity_usd")
        if equity is None:
            raise ConfigError(
                "account.starting_equity_usd is null — every position size "
                "derives from it, so sizing cannot proceed"
            )
        if not isinstance(equity, (int, float)) or equity <= 0:
            raise ConfigError(f"starting_equity_usd must be positive, got {equity!r}")

        if not acc.get("require_name_match"):
            raise ConfigError(
                "account.require_name_match must be true. It exists because an "
                "agent once selected a plausible-looking wrong account and "
                "reported its balance as the competition balance."
            )

        blocked = acc.get("blocklist_acc_ids") or []
        if str(acc_id) in {str(b) for b in blocked}:
            raise ConfigError(
                f"competition_acc_id {acc_id} also appears in blocklist_acc_ids"
            )

    def _validate_prohibitions(self) -> None:
        for path in _REQUIRED_FALSE:
            value = _dig(self.raw, path)
            if value is not False:
                raise ConfigError(
                    f"{'.'.join(path)} must be exactly false, got {value!r}"
                )

    def _validate_numerics(self) -> None:
        for path in _REQUIRED_NUMERIC:
            value = _dig(self.raw, path)
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise ConfigError(f"{'.'.join(path)} must be numeric, got {value!r}")
            if value <= 0:
                raise ConfigError(f"{'.'.join(path)} must be positive, got {value!r}")

    def _validate_universe_gates(self) -> None:
        symbols = _dig(self.raw, ("universe", "symbols"))
        if not symbols:
            raise ConfigError("universe.symbols is empty")

        gates = _dig(self.raw, ("fees", "min_atr20_usd"))
        if gates is None:
            raise ConfigError(
                "fees.min_atr20_usd is null — the per-trade cost gate cannot "
                "be evaluated, so no trade can be shown to clear its own costs"
            )
        if not isinstance(gates, dict):
            raise ConfigError(
                "fees.min_atr20_usd must be a per-symbol mapping, not a single "
                "value: the threshold depends on price and spread, so one "
                "number cannot be correct for both SPY and XLF"
            )
        missing = [s for s in symbols if s not in gates]
        if missing:
            raise ConfigError(
                f"fees.min_atr20_usd has no gate for {missing} — a tradable "
                f"symbol without a cost gate would bypass the check entirely"
            )

        spreads = _dig(self.raw, ("fees", "spread_round_trip_usd"))
        missing = [s for s in symbols if s not in (spreads or {})]
        if missing:
            raise ConfigError(f"fees.spread_round_trip_usd has no entry for {missing}")

    def _validate_coherence(self) -> None:
        """Catch limits that contradict each other.

        Individually valid numbers can still describe an impossible policy.
        These are the cases where a reviewer would have to multiply two values
        to notice the problem, which is exactly what gets missed.
        """
        sz = self.raw["sizing"]
        per_pos = sz["max_position_notional_pct"]
        max_pos = sz["max_concurrent_positions"]
        gross = sz["max_gross_exposure_pct"]

        if per_pos > gross:
            raise ConfigError(
                f"max_position_notional_pct ({per_pos}) exceeds "
                f"max_gross_exposure_pct ({gross}): a single position could "
                f"not be opened without breaching the gross limit"
            )
        if per_pos * max_pos < gross:
            # Not fatal, but the gross limit is then unreachable and therefore
            # not doing any work. Say so rather than leaving a decorative limit.
            self._inert_gross_limit = True
        else:
            self._inert_gross_limit = False

        ks = self.raw["kill_switch"]
        if ks["daily_loss_halt_pct"] >= ks["peak_drawdown_flatten_pct"]:
            raise ConfigError(
                f"daily_loss_halt_pct ({ks['daily_loss_halt_pct']}) must be "
                f"below peak_drawdown_flatten_pct "
                f"({ks['peak_drawdown_flatten_pct']}): the softer limit has to "
                f"trip first or the harder one is unreachable"
            )

        trigger = sz["stop_distance_atr_mult"]
        sizing_dist = sz["sizing_risk_distance_atr_mult"]
        if sizing_dist < trigger:
            raise ConfigError(
                f"sizing_risk_distance_atr_mult ({sizing_dist}) is below "
                f"stop_distance_atr_mult ({trigger}): sizing would assume the "
                f"stop fills better than its own trigger, which understates risk"
            )

    # ---------- accessors ----------

    @property
    def acc_id(self) -> str:
        return str(self.raw["account"]["competition_acc_id"])

    @property
    def account_name(self) -> str:
        return str(self.raw["account"]["expected_account_name"])

    @property
    def account_type(self) -> int | None:
        return self.raw["account"].get("expected_account_type")

    @property
    def blocklist(self) -> frozenset[str]:
        return frozenset(str(x) for x in (self.raw["account"].get("blocklist_acc_ids") or []))

    @property
    def symbols(self) -> tuple[str, ...]:
        return tuple(self.raw["universe"]["symbols"])

    @property
    def starting_equity(self) -> float:
        return float(self.raw["account"]["starting_equity_usd"])

    def min_atr20(self, symbol: str) -> float:
        return float(self.raw["fees"]["min_atr20_usd"][symbol])

    def spread(self, symbol: str) -> float:
        return float(self.raw["fees"]["spread_round_trip_usd"][symbol])

    def section(self, name: str) -> dict[str, Any]:
        return _dig(self.raw, (name,))

    @property
    def gross_limit_is_inert(self) -> bool:
        """True when max_gross_exposure_pct can never be reached, and is
        therefore not constraining anything."""
        return self._inert_gross_limit
