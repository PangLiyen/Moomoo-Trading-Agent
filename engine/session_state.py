"""Reconstructing live state from the ledger, and only from the ledger.

WHY THE LEDGER IS THE STATE

The driver previously passed `session_pnl_pct=0.0`, `peak_drawdown_pct=0.0`,
`consecutive_losses=0` and `seconds_since_last_entry=None` as literals. Every
kill-switch branch in the risk gate was therefore unreachable: the daily loss
halt, the drawdown flatten, the consecutive-loss stop, the cycle-gap flatten
and the entry spacing. The logic was correct and tested; nothing could ever
reach it.

The obvious repair is a state file. This module does not do that, because a
state file is a second source of truth that can disagree with the record — and
when it disagrees, the thing that decides is the file while the thing that
gets audited is the ledger. Divergence between them would be invisible and
would favour the undocumented side.

So state is derived from the ledger on every cycle. That makes the ledger
load-bearing rather than decorative: if a record is missing, the engine
notices immediately because its own state is wrong, instead of a reader
noticing weeks later.

RECORD TYPES THIS READS

    execute           an order was submitted (carries order_id, qty, stop)
    fill              an order filled (carries fill price and fee residual)
    exit              a position closed (carries the rule that fired)
    account_snapshot  equity at a point in time (peak and drawdown)
    cycle             a cycle completed (cadence, for the gap check)
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, time
from typing import Any, Iterable, Mapping, Sequence

from .config import RiskConfig
from .errors import IntegrityError
from .risk_gate import SessionState
from .signal import Position


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts)
    except ValueError:
        return None


@dataclass(frozen=True)
class LiveState:
    """Everything the gate and the exit logic need, derived from records."""

    session_state: SessionState
    positions: tuple[Position, ...]
    equity: float
    peak_equity: float
    realised_today: float
    closed_trades_today: int
    open_order_ids: tuple[str, ...]
    ledger_order_ids: frozenset[str]
    warnings: tuple[str, ...]

    def open_quantities(self) -> dict[str, int]:
        return {p.symbol: p.qty for p in self.positions if p.qty != 0}

    def to_ledger(self) -> dict[str, Any]:
        return {
            "equity": round(self.equity, 4),
            "peak_equity": round(self.peak_equity, 4),
            "drawdown_pct": round(
                (self.peak_equity - self.equity) / self.peak_equity * 100.0, 6
            ) if self.peak_equity > 0 else 0.0,
            "realised_today": round(self.realised_today, 4),
            "closed_trades_today": self.closed_trades_today,
            "open_positions": {
                p.symbol: {
                    "qty": p.qty,
                    "entry_price": p.entry_price,
                    "entry_atr20": p.entry_atr20,
                    "entry_time_et": p.entry_time_et.isoformat(),
                }
                for p in self.positions
            },
            "open_order_ids": list(self.open_order_ids),
            "warnings": list(self.warnings),
        }


def reconstruct(
    records: Sequence[Mapping[str, Any]],
    cfg: RiskConfig,
    *,
    now_et: datetime,
    broker_equity: float,
    broker_positions: Mapping[str, int],
    seconds_since_last_cycle_override: float | None = None,
    halted: bool = False,
    halt_reason: str = "",
) -> LiveState:
    """Build live state from ledger records plus the broker's own figures.

    Broker equity and positions are passed in rather than derived, and then
    **cross-checked** against the reconstruction. The ledger says what we did;
    the broker says what is. When they disagree the disagreement is the
    finding, so it is surfaced as a warning rather than silently resolved in
    either direction.
    """
    today = now_et.date()
    warnings: list[str] = []

    fills: list[Mapping[str, Any]] = []
    executes: dict[str, Mapping[str, Any]] = {}
    exits: list[Mapping[str, Any]] = []
    snapshots: list[Mapping[str, Any]] = []
    cycles: list[Mapping[str, Any]] = []
    order_ids: set[str] = set()

    for rec in records:
        stage = rec.get("stage")
        oid = rec.get("order_id")
        if oid:
            order_ids.add(str(oid))
        if stage == "execute":
            if oid:
                executes[str(oid)] = rec
        elif stage == "fill":
            fills.append(rec)
        elif stage == "exit":
            exits.append(rec)
        elif stage == "account_snapshot":
            snapshots.append(rec)
        elif stage == "cycle":
            cycles.append(rec)

    # ---- open positions: buy fills net of sell fills, per symbol ----
    net: dict[str, int] = {}
    entry_of: dict[str, Mapping[str, Any]] = {}
    for f in sorted(fills, key=lambda r: str(r.get("ts_utc", ""))):
        if str(f.get("verdict")) not in {"FILLED", "PARTIAL"}:
            continue
        sym = str(f.get("symbol") or "")
        qty = int(f.get("filled_qty") or 0)
        if not sym or qty <= 0:
            continue
        side = str(f.get("side", "")).upper()
        if side == "BUY":
            if net.get(sym, 0) == 0:
                entry_of[sym] = f          # opening fill of a new position
            net[sym] = net.get(sym, 0) + qty
        elif side == "SELL":
            net[sym] = net.get(sym, 0) - qty
            if net[sym] <= 0:
                entry_of.pop(sym, None)

    positions: list[Position] = []
    for sym, qty in net.items():
        if qty <= 0:
            continue
        opening = entry_of.get(sym)
        if opening is None:
            warnings.append(
                f"{sym}: {qty} held with no opening fill in the ledger; "
                f"exit levels cannot be derived"
            )
            continue
        exec_rec = executes.get(str(opening.get("order_id")), {})
        entry_price = float(opening.get("avg_fill_price") or 0)
        entry_atr = float(exec_rec.get("entry_atr20") or 0)
        entry_time = _parse(opening.get("fill_time_et")) or now_et
        if entry_price <= 0 or entry_atr <= 0:
            warnings.append(
                f"{sym}: opening fill lacks entry price or ATR "
                f"(price={entry_price}, atr={entry_atr}); stop level unknown"
            )
            continue
        positions.append(Position(symbol=sym, qty=qty, entry_price=entry_price,
                                  entry_atr20=entry_atr,
                                  entry_time_et=entry_time))

    # ---- cross-check against the broker ----
    derived = {p.symbol: p.qty for p in positions}
    broker = {s: int(q) for s, q in broker_positions.items() if int(q) != 0}
    if derived != broker:
        warnings.append(
            f"position mismatch: ledger derives {derived or '{}'}, broker "
            f"reports {broker or '{}'}. The ledger says what we did and the "
            f"broker says what is; this disagreement is itself the finding."
        )

    # ---- equity, peak, drawdown ----
    recorded_peak = max(
        (float(s.get("equity") or 0) for s in snapshots), default=0.0
    )
    peak = max(recorded_peak, broker_equity, cfg.starting_equity)

    # ---- realised P&L and trade outcomes for today ----
    todays_exits = [
        e for e in exits
        if (_parse(e.get("exit_time_et")) or now_et).date() == today
    ]
    realised_today = sum(float(e.get("realised_pnl") or 0) for e in todays_exits)

    # consecutive losses run backwards through ALL exits, not only today's: a
    # losing streak does not reset because the clock struck midnight.
    consecutive = 0
    for e in reversed(sorted(exits, key=lambda r: str(r.get("ts_utc", "")))):
        pnl = float(e.get("realised_pnl") or 0)
        if pnl < 0:
            consecutive += 1
        else:
            break

    # ---- cadence and frequency ----
    todays_entry_fills = [
        f for f in fills
        if str(f.get("side", "")).upper() == "BUY"
        and str(f.get("verdict")) in {"FILLED", "PARTIAL"}
        and (_parse(f.get("fill_time_et")) or now_et).date() == today
    ]
    last_entry = max(
        (_parse(f.get("fill_time_et")) for f in todays_entry_fills
         if _parse(f.get("fill_time_et"))),
        default=None,
    )
    seconds_since_entry = (
        (now_et - last_entry).total_seconds() if last_entry else None
    )

    todays_cycles = [
        c for c in cycles
        if str(c.get("cycle_id", "")).startswith(f"C{today:%Y%m%d}")
    ]
    if seconds_since_last_cycle_override is not None:
        gap = seconds_since_last_cycle_override
    elif todays_cycles:
        last_cycle_id = str(todays_cycles[-1].get("cycle_id", ""))
        try:
            last_cycle = datetime.strptime(last_cycle_id[1:], "%Y%m%dT%H%M")
            gap = (now_et - last_cycle).total_seconds()
        except ValueError:
            gap = 0.0
            warnings.append(f"unparseable cycle_id {last_cycle_id!r}")
    else:
        gap = 0.0   # first cycle of the session; no gap to measure

    orders_today = sum(
        1 for r in records
        if r.get("stage") == "execute"
        and str(r.get("cycle_id", "")).startswith(f"C{today:%Y%m%d}")
    )

    session_pnl_pct = (
        (broker_equity - cfg.starting_equity) / cfg.starting_equity * 100.0
        if cfg.starting_equity else 0.0
    )
    drawdown_pct = (peak - broker_equity) / peak * 100.0 if peak > 0 else 0.0

    state = SessionState(
        now_et=now_et,
        entries_this_session=len(todays_entry_fills),
        orders_this_session=orders_today,
        seconds_since_last_entry=seconds_since_entry,
        seconds_since_last_completed_cycle=gap,
        session_pnl_pct=session_pnl_pct,
        peak_drawdown_pct=drawdown_pct,
        consecutive_losses=consecutive,
        halted=halted,
        halt_reason=halt_reason,
    )

    open_ids = tuple(sorted(
        oid for oid, rec in executes.items()
        if not any(str(f.get("order_id")) == oid
                   and str(f.get("verdict")) in
                   {"FILLED", "REJECTED", "CANCELLED"}
                   for f in fills)
    ))

    return LiveState(
        session_state=state,
        positions=tuple(positions),
        equity=broker_equity,
        peak_equity=peak,
        realised_today=realised_today,
        closed_trades_today=len(todays_exits),
        open_order_ids=open_ids,
        ledger_order_ids=frozenset(order_ids),
        warnings=tuple(warnings),
    )
