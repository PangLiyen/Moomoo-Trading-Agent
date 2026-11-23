"""Exits, order intents, and the records that make the ledger evaluable.

WHAT THIS CLOSES

`STRATEGY.md` section 4 promises five exits. Before this module
`evaluate_exit` had zero callers and zero tests: a position opened would have
been held until a human intervened, and the pre-registration's "no position
held past 15:50 ET" would have failed by absence rather than by variance.

It also closes the calendar. `calendar_overrides` was never read, so 27
November — a half day closing at 13:00 ET, with the deck warning that no reset
cards are available — would have been traded as a normal session.

WHAT THIS DELIBERATELY DOES NOT DO

It does not submit orders. There is no network call anywhere in this module,
and that is the architecture rather than an omission: the component that
decides what to trade has no ability to trade, so a defect here cannot reach
the broker. It emits intents; something with credentials submits them and
reports back.

That boundary is the submission's central claim, so it is enforced by
structure. `test_executor_has_no_network_imports` fails if an import appears.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, asdict, field
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .config import RiskConfig
from .errors import ConfigError, RiskViolation
from .ledger import Ledger
from .signal import Indicators, Position, SignalVerdict, evaluate_exit

CLOSED = "closed"
HALF_DAY = "half_day"


# ---------------------------------------------------------------------------
# Calendar
# ---------------------------------------------------------------------------

def _parse_et(value: str) -> time:
    hh, mm = str(value).split(":")
    return time(int(hh), int(mm))


@dataclass(frozen=True)
class SessionPlan:
    """What today is, according to config rather than according to a default."""

    trading_date: date
    is_trading_day: bool
    status: str
    earliest_entry_et: time
    latest_entry_et: time
    flat_by_et: time
    market_close_et: time | None
    note: str

    def to_ledger(self) -> dict[str, Any]:
        d = asdict(self)
        d["trading_date"] = self.trading_date.isoformat()
        for k in ("earliest_entry_et", "latest_entry_et", "flat_by_et"):
            d[k] = getattr(self, k).isoformat(timespec="minutes")
        d["market_close_et"] = (
            self.market_close_et.isoformat(timespec="minutes")
            if self.market_close_et else None
        )
        return d


def plan_session(cfg: RiskConfig, now_et: datetime) -> SessionPlan:
    """Resolve today's windows from config, honouring calendar overrides.

    Every value comes from the YAML. The previous implementation used function
    defaults that happened to match the config, which is indistinguishable
    from correct until someone edits the config and nothing changes.
    """
    sess = cfg.section("session")
    today = now_et.date()

    flat_by = _parse_et(sess["flatten_all_by_et"])
    earliest = _parse_et(sess["earliest_entry_et"])
    latest = _parse_et(cfg.section("frequency")["no_new_entries_after_et"])
    status, note, close_et = "regular", "", None

    for entry in (cfg.raw.get("calendar_overrides") or []):
        if str(entry.get("date")) != today.isoformat():
            continue
        status = str(entry.get("status") or "regular")
        note = str(entry.get("note") or "")
        if entry.get("market_close_et"):
            close_et = _parse_et(entry["market_close_et"])
        if entry.get("flatten_all_by_et"):
            flat_by = _parse_et(entry["flatten_all_by_et"])
        break

    # A weekend is not a "regular" session. The first version left the label
    # at its default and only flipped is_trading_day, which wrote the
    # self-contradictory record `2026-11-22 is regular` with is_trading_day
    # false -- into a hash chain, where it cannot be quietly corrected later.
    if today.weekday() > 4 and status == "regular":
        status = "weekend"
        note = note or f"{today:%A}: US equities closed"

    is_trading = status not in {CLOSED, "weekend"} and today.weekday() <= 4

    # On a short session the entry cutoff must move with the flat-by, or the
    # engine would open a position 20 minutes before a 13:00 close carrying a
    # 60-minute time stop that cannot run.
    #
    # An earlier version computed a `cutoff_from_close` here by subtracting a
    # value from itself and never using the result. It did nothing, and read
    # as though it did something, which is worse than absent.
    if flat_by < latest:
        latest = min(latest, flat_by)

    return SessionPlan(
        trading_date=today,
        is_trading_day=is_trading,
        status=status,
        earliest_entry_et=earliest,
        latest_entry_et=latest,
        flat_by_et=flat_by,
        market_close_et=close_et,
        note=note,
    )


# ---------------------------------------------------------------------------
# Exits
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ExitIntent:
    symbol: str
    qty: int
    exit_rule: str
    reason_text: str
    stop_level: float
    target_level: float
    entry_price: float
    entry_atr20: float
    held_minutes: float
    reference_price: float
    urgency: str                      # "immediate" | "normal"
    detail: dict[str, Any] = field(default_factory=dict)

    def to_ledger(self) -> dict[str, Any]:
        return asdict(self)


def plan_exits(
    positions: Sequence[Position],
    indicators: Mapping[str, Indicators],
    cfg: RiskConfig,
    *,
    now_et: datetime,
    plan: SessionPlan,
) -> tuple[list[ExitIntent], list[SignalVerdict]]:
    """Evaluate every open position. Returns (intents, all verdicts).

    All verdicts are returned, not only the exits, because a HOLD is a
    decision about a live position and belongs in the ledger. A log that
    records only the cycles where something happened cannot show the system
    was watching in between.

    A position with no indicator data is an **immediate** exit, not a hold.
    Being unable to evaluate a stop is strictly worse than exiting: the stop
    is software-enforced, so no data means no protection.
    """
    sz = cfg.section("sizing")
    exe = cfg.section("execution")
    stop_mult = float(sz["stop_distance_atr_mult"])
    max_hold = int(exe["max_holding_minutes"])

    intents: list[ExitIntent] = []
    verdicts: list[SignalVerdict] = []

    for pos in positions:
        ind = indicators.get(pos.symbol)
        if ind is None:
            intents.append(ExitIntent(
                symbol=pos.symbol, qty=pos.qty, exit_rule="NO_DATA",
                reason_text=(
                    "no current indicator data for an open position; the stop "
                    "is software-enforced, so unevaluable means unprotected"
                ),
                stop_level=pos.entry_price - stop_mult * pos.entry_atr20,
                target_level=float("nan"), entry_price=pos.entry_price,
                entry_atr20=pos.entry_atr20,
                held_minutes=(now_et - pos.entry_time_et).total_seconds() / 60.0,
                reference_price=pos.entry_price, urgency="immediate",
            ))
            continue

        verdict = evaluate_exit(
            pos, ind, now_et=now_et,
            stop_atr_mult=stop_mult,
            max_holding_minutes=max_hold,
            session_flat_by=plan.flat_by_et,
            holiday_flat_by=plan.flat_by_et,
        )
        verdicts.append(verdict)
        if not verdict.qualifies:
            continue

        intents.append(ExitIntent(
            symbol=pos.symbol, qty=pos.qty, exit_rule=verdict.reason_code,
            reason_text=verdict.reason_text,
            stop_level=float(verdict.detail["stop_level"]),
            target_level=float(verdict.detail["target_sma20"]),
            entry_price=pos.entry_price, entry_atr20=pos.entry_atr20,
            held_minutes=float(verdict.detail["held_minutes"]),
            reference_price=float(verdict.detail["close"]),
            urgency="immediate" if verdict.reason_code in
                    {"STOP_HIT", "SESSION_STOP"} else "normal",
            detail=dict(verdict.detail),
        ))

    return intents, verdicts


# ---------------------------------------------------------------------------
# Order intents
# ---------------------------------------------------------------------------

def build_order(
    cfg: RiskConfig,
    *,
    symbol: str,
    side: str,
    qty: int,
    limit_price: float,
    cycle_id: str,
    ledger_tip: str,
    tag_prefix: str = "S01",
) -> dict[str, Any]:
    """The exact JSON to submit, with the ledger hash stamped into `text`.

    `text` holds 100 bytes on moomoo's own order record, so the decision's
    chain position is written somewhere we cannot edit. That is what closes
    the one hole the hash chain cannot cover itself: truncating the chain's
    tail leaves a self-consistent file, but the broker still holds hashes for
    orders the ledger no longer mentions.
    """
    if side not in {"BUY", "SELL"}:
        raise ValueError(f"side must be BUY or SELL, got {side!r}")
    if qty <= 0:
        raise ValueError(f"qty must be positive, got {qty}")

    exe = cfg.section("execution")
    text = f"{tag_prefix}-{cycle_id}-{ledger_tip[:8]}"
    if len(text.encode()) > 100:
        text = text[:100]

    return {
        "acc_id": cfg.acc_id,
        "market": int(cfg.raw["account"]["market_id"]),
        "symbol": symbol,
        "order_type": int(exe["order_type"]),
        "order_side": 1 if side == "BUY" else 2,
        "qty": str(qty),
        "price": f"{limit_price:.2f}",
        "order_trade_time_type": int(exe["order_trade_time_type"]),
        "text": text,
    }


def crossing_limit(
    cfg: RiskConfig, *, side: str, bid: float, ask: float, urgency: str
) -> float:
    """Limit price for an order intended to fill.

    Measured four times during Session 00: an aggressive limit fills at
    prevailing market, not at the limit, so pricing through the book buys
    certainty of execution without paying the offset. For an immediate exit
    that certainty is the point — a stop that does not fill is not a stop.
    """
    through = float(cfg.section("execution").get("exit_cross_offset_pct", 0.1)) / 100.0
    if urgency == "immediate":
        return ask * (1 + through) if side == "BUY" else bid * (1 - through)
    return ask if side == "BUY" else bid


# ---------------------------------------------------------------------------
# Records — the three types that make P2, P6 and P7 evaluable
# ---------------------------------------------------------------------------

def record_submission(
    led: Ledger, *, cycle_id: str, order: Mapping[str, Any], intent: str,
    approved_qty: int, exit_intent: ExitIntent | None = None,
    proposal: Mapping[str, Any] | None = None, order_id: str | None = None,
) -> dict[str, Any]:
    """Written at submission, before the outcome is known.

    `approved_qty` and the submitted qty are both recorded so a mismatch is
    detectable within one cycle. Section 8 forbids the executor increasing
    size; nothing could previously verify it, because the approval and the
    submission were never written down together.
    """
    submitted = int(order["qty"])
    rec: dict[str, Any] = {
        "cycle_id": cycle_id, "stage": "execute", "verdict": "SUBMITTED",
        "reason_code": "ENTRY" if intent == "ENTRY" else "EXIT",
        "reason_text": f"{intent.lower()} order submitted for {order['symbol']}",
        "symbol": order["symbol"],
        "side": "BUY" if int(order["order_side"]) == 1 else "SELL",
        "intent": intent,
        "approved_qty": approved_qty,
        "submitted_qty": submitted,
        "qty_matches_approval": submitted == approved_qty,
        "limit_price": float(order["price"]),
        "order": dict(order),
    }
    if order_id:
        rec["order_id"] = str(order_id)
    if exit_intent is not None:
        rec["exit_rule"] = exit_intent.exit_rule
        rec["stop_level"] = exit_intent.stop_level
        rec["entry_atr20"] = exit_intent.entry_atr20
        rec["entry_price"] = exit_intent.entry_price
    if proposal is not None:
        rec["proposal"] = dict(proposal)
        rec["entry_atr20"] = float(proposal.get("atr20") or 0)
    return led.append(rec)


def implied_fee(
    *, cash_before: float, cash_after: float, filled_qty: int,
    avg_fill_price: float, side: str,
) -> float:
    """Fee inferred from the cash delta around a fill.

    `account_order_fees` and `account_cash_logs` both return MCP error -32603,
    so there is no fee field to read. This is the measurement that replaced
    them: a buy of 1 SPY at 706.425 moved balance 99285.340 -> 99990.765 on
    the sell side, implying exactly 1.0000 against a 0.99 reservation.
    """
    gross = filled_qty * avg_fill_price
    if side.upper() == "BUY":
        return (cash_before - cash_after) - gross
    return gross - (cash_after - cash_before)


def record_fill(
    led: Ledger, *, cycle_id: str, order_id: str, symbol: str, side: str,
    verdict: str, filled_qty: int, avg_fill_price: float, fill_time_et: str,
    limit_price: float | None = None, cash_before: float | None = None,
    cash_after: float | None = None, submitted_at_et: str | None = None,
) -> dict[str, Any]:
    """Written when the broker confirms. Carries the fee residual and the
    price improvement, labelled separately so neither is read as the other."""
    rec: dict[str, Any] = {
        "cycle_id": cycle_id, "stage": "fill", "verdict": verdict,
        "reason_code": "FILL" if verdict in {"FILLED", "PARTIAL"} else verdict,
        "reason_text": f"{side} {filled_qty} {symbol} at {avg_fill_price}",
        "order_id": str(order_id), "symbol": symbol, "side": side.upper(),
        "filled_qty": int(filled_qty), "avg_fill_price": float(avg_fill_price),
        "fill_time_et": fill_time_et,
    }
    if limit_price is not None and filled_qty:
        improvement = (limit_price - avg_fill_price) if side.upper() == "BUY" \
            else (avg_fill_price - limit_price)
        rec["limit_price"] = float(limit_price)
        # Named for what it is. Logging this as slippage would show the agent
        # beating the market on every trade, which is both wrong and flattering.
        rec["price_improvement_vs_limit"] = round(improvement, 6)
    if cash_before is not None and cash_after is not None and filled_qty:
        rec["cash_before"] = float(cash_before)
        rec["cash_after"] = float(cash_after)
        rec["implied_fee"] = round(implied_fee(
            cash_before=cash_before, cash_after=cash_after,
            filled_qty=filled_qty, avg_fill_price=avg_fill_price, side=side,
        ), 6)
    if submitted_at_et:
        rec["submitted_at_et"] = submitted_at_et
    return led.append(rec)


def record_exit(
    led: Ledger, *, cycle_id: str, intent: ExitIntent, exit_price: float,
    exit_time_et: str, filled_qty: int, fees: float = 0.0,
) -> dict[str, Any]:
    """Written when a position closes, carrying the rule that fired.

    P7 is "median stop breach under 0.5 ATR". Without the rule recorded, a
    stop cannot be distinguished from a time exit that happened to print below
    the stop level, and the prediction is unmeasurable. The label is the
    difference between a measurement and a guess.
    """
    gross = (exit_price - intent.entry_price) * filled_qty
    breach = None
    if intent.exit_rule == "STOP_HIT" and intent.entry_atr20 > 0:
        breach = round((intent.stop_level - exit_price) / intent.entry_atr20, 6)

    return led.append({
        "cycle_id": cycle_id, "stage": "exit", "verdict": "EXIT",
        "reason_code": intent.exit_rule, "reason_text": intent.reason_text,
        "symbol": intent.symbol, "exit_rule": intent.exit_rule,
        "qty": int(filled_qty), "entry_price": intent.entry_price,
        "exit_price": float(exit_price), "stop_level": intent.stop_level,
        "target_level": intent.target_level, "entry_atr20": intent.entry_atr20,
        "held_minutes": round(intent.held_minutes, 2),
        "gross_pnl": round(gross, 6), "fees": round(fees, 6),
        "realised_pnl": round(gross - fees, 6),
        "breach_past_stop_atr": breach,
        "exit_time_et": exit_time_et,
    })


def record_account_snapshot(
    led: Ledger, *, cycle_id: str, live_state: Any,
) -> dict[str, Any]:
    """Equity, peak and drawdown, every cycle.

    P2 is "max drawdown below 0.6%". Nothing recorded equity on any cycle
    record, and the broker exposes only a current snapshot with no history, so
    the prediction could not have been evaluated at all. This is the record
    that makes it a measurement.
    """
    return led.append({
        "cycle_id": cycle_id, "stage": "account_snapshot",
        "verdict": "OBSERVED", "reason_code": "EQUITY_SNAPSHOT",
        "reason_text": "equity, peak and drawdown at cycle time",
        **live_state.to_ledger(),
    })


def write_intents(path: str | Path, cycle_id: str, orders: Sequence[Mapping[str, Any]]) -> Path:
    """Emit the orders for something with credentials to submit.

    A file rather than a call. The decision component cannot reach the broker
    even if it is wrong, which is the property the submission claims.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(
        {"cycle_id": cycle_id, "orders": [dict(o) for o in orders]},
        indent=2,
    ), encoding="utf-8")
    return p
