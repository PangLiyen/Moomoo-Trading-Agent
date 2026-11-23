"""The risk gate: the last thing between a proposal and an order.

DESIGN INVARIANT

The gate may only ever make a proposal smaller, or refuse it. It can never
enlarge one, loosen a limit, or invent a trade. This is enforced at the end of
`evaluate` by an assertion on the returned quantity, not merely intended by
the code above it — an invariant that depends on every branch being written
correctly is not an invariant.

WHY A SEPARATE MODULE

The strategy proposes; this decides. Keeping them apart means the component
that can be influenced by market data, news text or an operator's prompt is
not the component holding the limits. The gate reads only numbers: a
quantity, a price, counts, timestamps. It never reads free text, so there is
no text that can argue with it.

VERDICTS

  APPROVE  proposal passes unchanged
  SHRINK   proposal exceeds a budget but a smaller version is valid
  BLOCK    no valid version of this proposal exists right now

A BLOCK is a decision, not an error. It is written to the ledger with the same
detail as a fill, because a system that only records what it did — and not
what it declined to do — cannot demonstrate that its limits were ever load
bearing.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, time
from typing import Any, Literal, Sequence

from .config import RiskConfig
from .errors import AccountGuardError, RiskViolation, StaleDataError
from .sizing import SizingResult

Verdict = Literal["APPROVE", "SHRINK", "BLOCK"]


@dataclass(frozen=True)
class AccountState:
    """Everything the gate needs to know about the account, as numbers."""

    acc_id: str
    account_title: str
    account_type: int | None
    total_asset: float
    balance: float
    hold: float
    market_value: float
    open_positions: dict[str, int] = field(default_factory=dict)
    # Order ids present in the account that our ledger cannot account for.
    unattributed_order_ids: tuple[str, ...] = ()

    def reconciles(self) -> bool:
        """total_asset = balance + mv + hold, measured to hold exactly."""
        return abs((self.balance + self.market_value + self.hold) - self.total_asset) < 0.005


@dataclass(frozen=True)
class SessionState:
    """Per-session counters and switches. Supplied by the caller, never
    inferred by the gate — the gate does not keep state between calls."""

    now_et: datetime
    entries_this_session: int
    orders_this_session: int
    seconds_since_last_entry: float | None
    seconds_since_last_completed_cycle: float
    session_pnl_pct: float
    peak_drawdown_pct: float
    consecutive_losses: int
    halted: bool = False
    halt_reason: str = ""


@dataclass(frozen=True)
class GateDecision:
    verdict: Verdict
    reason_code: str
    reason_text: str
    qty_in: int
    qty_out: int
    checks_passed: tuple[str, ...]
    check_detail: dict[str, Any] = field(default_factory=dict)

    def to_ledger(self) -> dict[str, Any]:
        return asdict(self)


def _parse_et(value: str) -> time:
    hh, mm = value.split(":")
    return time(int(hh), int(mm))


class RiskGate:
    def __init__(self, cfg: RiskConfig):
        self.cfg = cfg

    # ------------------------------------------------------------------
    # Account guard. Runs before anything else, on every cycle.
    # ------------------------------------------------------------------
    def assert_account(self, acct: AccountState) -> None:
        """Three independent checks, all of which must agree.

        One check is a single point of failure. The account title is editable
        in the broker UI; an id can be mistyped; a type is undocumented. Any
        one of them alone is weak, and requiring all three means a wrong
        account has to coincidentally match on every axis.
        """
        if acct.acc_id in self.cfg.blocklist:
            raise AccountGuardError(
                f"acc_id {acct.acc_id} is explicitly blocklisted"
            )
        if acct.acc_id != self.cfg.acc_id:
            raise AccountGuardError(
                f"acc_id {acct.acc_id} is not the competition account "
                f"{self.cfg.acc_id} — trades here do not count toward ranking"
            )
        if acct.account_title != self.cfg.account_name:
            raise AccountGuardError(
                f"account title {acct.account_title!r} does not match the "
                f"expected {self.cfg.account_name!r}"
            )
        expected_type = self.cfg.account_type
        if expected_type is not None and acct.account_type != expected_type:
            raise AccountGuardError(
                f"account_type {acct.account_type} != expected {expected_type}"
            )
        if not acct.reconciles():
            raise RiskViolation(
                "ACCOUNT_SNAPSHOT_INCONSISTENT",
                f"balance {acct.balance} + mv {acct.market_value} + hold "
                f"{acct.hold} does not equal total_asset {acct.total_asset}; "
                f"the snapshot is mid-transition and must not be sized against",
            )

    def assert_no_foreign_activity(self, acct: AccountState) -> None:
        """Halt on any order we cannot attribute to our own ledger.

        The hash chain proves our records were not edited. It says nothing
        about orders we never placed. If a second actor is writing to this
        account, P&L cannot be attributed to our decisions, and attribution is
        the whole basis of the result.
        """
        integ = self.cfg.raw.get("account_integrity", {})
        if not integ.get("reconcile_order_list_every_cycle"):
            return
        known = {str(x) for x in (integ.get("known_foreign_order_ids") or [])}
        novel = [o for o in acct.unattributed_order_ids if str(o) not in known]
        if novel:
            raise RiskViolation(
                "FOREIGN_ORDER_DETECTED",
                f"orders present in {acct.acc_id} that our ledger cannot "
                f"account for: {novel}. A second writer makes P&L "
                f"unattributable. Halting rather than averaging someone "
                f"else's trades into our own reported performance.",
            )

    # ------------------------------------------------------------------
    # Entry evaluation
    # ------------------------------------------------------------------
    def evaluate_entry(
        self,
        proposal: SizingResult,
        *,
        acct: AccountState,
        sess: SessionState,
        quote_age_seconds: float,
        spread_pct_of_mid: float,
    ) -> GateDecision:
        passed: list[str] = []
        detail: dict[str, Any] = {}
        qty_in = proposal.qty

        def block(code: str, text: str) -> GateDecision:
            return GateDecision("BLOCK", code, text, qty_in, 0, tuple(passed), detail)

        # --- hard stops first: cheapest checks, and the ones that mean "stop" ---
        if sess.halted:
            return block("MANUAL_HALT", f"session halted: {sess.halt_reason}")
        passed.append("not_halted")

        ks = self.cfg.section("kill_switch")
        if sess.session_pnl_pct <= -abs(float(ks["daily_loss_halt_pct"])):
            return block(
                "DAILY_LOSS_HALT",
                f"session P&L {sess.session_pnl_pct:.2f}% breached "
                f"-{ks['daily_loss_halt_pct']}%; no new entries",
            )
        if sess.peak_drawdown_pct >= abs(float(ks["peak_drawdown_flatten_pct"])):
            return block(
                "DRAWDOWN_FLATTEN",
                f"peak drawdown {sess.peak_drawdown_pct:.2f}% breached "
                f"{ks['peak_drawdown_flatten_pct']}%",
            )
        if sess.consecutive_losses >= int(ks["consecutive_losses_stop_session"]):
            return block(
                "CONSECUTIVE_LOSS_STOP",
                f"{sess.consecutive_losses} consecutive losses",
            )
        passed.append("kill_switch_clear")

        # --- cycle-gap hazard ---
        # The hazard is an UNMONITORED POSITION, not elapsed time. With
        # software-only stops, a skipped cycle means an open position went
        # unwatched, so no more are opened until cadence is restored.
        #
        # With a flat book there is nothing unmonitored, and blocking there
        # would mean the first entry of a session could never be taken unless
        # a cycle had already run minutes earlier. That is not a tightening,
        # it is an inability to start.
        #
        # So the gap blocks when a position is open, and also when opening
        # WOULD create one that the observed cadence cannot protect: if the
        # last gap exceeded the limit, the next one may too, and a position
        # opened now would be the thing left unwatched.
        max_gap = ks.get("max_cycle_gap_seconds")
        gap = sess.seconds_since_last_completed_cycle
        holding = any(q != 0 for q in acct.open_positions.values())
        # Read from config, not hardcoded. The first version of this scoping
        # asserted the policy in a config key and then ignored it, which is
        # the N4 pattern -- dead config describing behaviour it does not
        # control -- written four messages after N4 was documented.
        only_while_holding = bool(ks.get("cycle_gap_blocks_only_while_holding", True))
        gap_blocks = holding or not only_while_holding
        if max_gap and gap > float(max_gap) and gap_blocks:
            return block(
                "CYCLE_GAP_EXCEEDED",
                f"{gap:.0f}s since the last completed cycle exceeds "
                f"{max_gap}s while holding {dict(acct.open_positions)}. With "
                f"software-only stops that position went unmonitored; not "
                f"opening more until cadence is restored.",
            )
        detail["cycle_gap_seconds"] = gap
        detail["cycle_gap_limit_seconds"] = float(max_gap) if max_gap else None
        detail["holding_during_gap"] = holding
        detail["cycle_gap_blocks_only_while_holding"] = only_while_holding
        passed.append("cycle_cadence_ok")

        # --- data quality: refuse to act on data we cannot date ---
        # A negative age is a defect, not a decision: raise, because a BLOCK
        # record would be written as if the data had been read. Too-old stays
        # a recorded BLOCK -- the quote is real, just late -- and is also
        # asserted upstream by signal.evaluate_entry.
        if not (quote_age_seconds >= 0):
            raise StaleDataError(
                f"quote age {quote_age_seconds!r}s is negative: the timestamp "
                f"cannot be placed on the clock, so freshness is unproven"
            )
        dq = self.cfg.section("data_quality")
        if quote_age_seconds > float(dq["max_quote_age_seconds"]):
            return block(
                "STALE_QUOTE",
                f"quote age {quote_age_seconds:.0f}s > "
                f"{dq['max_quote_age_seconds']}s",
            )
        if spread_pct_of_mid > float(dq["max_spread_pct_of_mid"]):
            return block(
                "SPREAD_TOO_WIDE",
                f"spread {spread_pct_of_mid:.4f}% of mid > "
                f"{dq['max_spread_pct_of_mid']}%",
            )
        passed.append("data_fresh")
        detail["quote_age_seconds"] = quote_age_seconds
        detail["spread_pct_of_mid"] = spread_pct_of_mid

        # --- session window ---
        freq = self.cfg.section("frequency")
        cutoff = _parse_et(str(freq["no_new_entries_after_et"]))
        if sess.now_et.time() >= cutoff:
            return block(
                "AFTER_ENTRY_CUTOFF",
                f"{sess.now_et.time().isoformat(timespec='minutes')} ET is past "
                f"the {cutoff.isoformat(timespec='minutes')} entry cutoff",
            )
        passed.append("within_entry_window")

        # --- frequency budgets ---
        if sess.entries_this_session >= int(freq["max_entries_per_session"]):
            return block(
                "ENTRY_BUDGET_EXHAUSTED",
                f"{sess.entries_this_session} entries already taken "
                f"(limit {freq['max_entries_per_session']})",
            )
        if sess.orders_this_session >= int(freq["max_orders_per_session"]):
            return block(
                "ORDER_BUDGET_EXHAUSTED",
                f"{sess.orders_this_session} orders already placed "
                f"(limit {freq['max_orders_per_session']})",
            )
        min_gap = freq.get("min_seconds_between_entries")
        if (
            min_gap
            and sess.seconds_since_last_entry is not None
            and sess.seconds_since_last_entry < float(min_gap)
        ):
            return block(
                "ENTRY_TOO_SOON",
                f"{sess.seconds_since_last_entry:.0f}s since last entry < "
                f"{min_gap}s minimum",
            )
        passed.append("frequency_budget_ok")

        # --- position and correlation limits ---
        sz = self.cfg.section("sizing")
        held = {s: q for s, q in acct.open_positions.items() if q != 0}

        if proposal.symbol in held:
            return block(
                "ALREADY_HOLDING",
                f"already long {held[proposal.symbol]} of {proposal.symbol}; "
                f"this strategy does not add to positions",
            )
        if len(held) >= int(sz["max_concurrent_positions"]):
            return block(
                "POSITION_COUNT_LIMIT",
                f"{len(held)} positions open (limit "
                f"{sz['max_concurrent_positions']})",
            )

        for name, cluster in (self.cfg.section("universe").get("clusters") or {}).items():
            members = set(cluster.get("members") or [])
            if proposal.symbol not in members:
                continue
            in_cluster = [s for s in held if s in members]
            if len(in_cluster) >= int(cluster.get("max_concurrent_positions", 1)):
                return block(
                    "CORRELATION_CLUSTER_LIMIT",
                    f"{proposal.symbol} is in cluster {name!r}, which already "
                    f"holds {in_cluster}. These instruments move together, so "
                    f"two positions is one bet at double size.",
                )
        passed.append("position_limits_ok")

        # --- gross exposure: the only check that may SHRINK rather than block ---
        gross_cap = acct.total_asset * float(sz["max_gross_exposure_pct"]) / 100.0
        gross_now = acct.market_value
        headroom = gross_cap - gross_now
        detail["gross_exposure_usd"] = round(gross_now, 2)
        detail["gross_cap_usd"] = round(gross_cap, 2)

        qty_out = qty_in
        verdict: Verdict = "APPROVE"
        reason_code, reason_text = "APPROVED", "all risk checks passed"

        if proposal.notional_usd > headroom:
            affordable = int(headroom // proposal.price) if headroom > 0 else 0
            if affordable < int(sz.get("min_qty", 1)):
                return block(
                    "GROSS_EXPOSURE_LIMIT",
                    f"gross exposure headroom ${headroom:,.2f} will not fund "
                    f"even one share at ${proposal.price:,.2f}",
                )
            qty_out = affordable
            verdict = "SHRINK"
            reason_code = "GROSS_EXPOSURE_SHRINK"
            reason_text = (
                f"reduced {qty_in} -> {qty_out} to stay inside gross exposure "
                f"cap (headroom ${headroom:,.2f})"
            )
        passed.append("gross_exposure_ok")

        # --- buying power, measured against cash and never against margin ---
        acct_cfg = self.cfg.section("account")
        if acct_cfg.get("forbid_sizing_off_buying_power"):
            commission = float(self.cfg.section("fees")["commission_per_filled_order_usd"])
            required = qty_out * proposal.price + commission
            if required > acct.balance:
                affordable = int((acct.balance - commission) // proposal.price)
                if affordable < int(sz.get("min_qty", 1)):
                    return block(
                        "INSUFFICIENT_CASH",
                        f"${required:,.2f} required exceeds available cash "
                        f"${acct.balance:,.2f}. Margin headroom is ignored by "
                        f"policy; this is not a reason to use it.",
                    )
                qty_out = min(qty_out, affordable)
                verdict = "SHRINK"
                reason_code = "CASH_SHRINK"
                reason_text = f"reduced {qty_in} -> {qty_out} to stay inside cash balance"
        passed.append("cash_cover_ok")

        # --- the invariant, enforced rather than assumed ---
        if qty_out > qty_in:
            raise AssertionError(
                f"risk gate increased quantity {qty_in} -> {qty_out}; the gate "
                f"may only tighten. This is a bug and must halt the engine."
            )
        if qty_out < 0:
            raise AssertionError(f"risk gate produced negative quantity {qty_out}")

        return GateDecision(
            verdict, reason_code, reason_text, qty_in, qty_out, tuple(passed), detail
        )
