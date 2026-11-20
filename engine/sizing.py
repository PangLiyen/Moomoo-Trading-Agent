"""Deterministic position sizing.

The whole point of this module is that it contains no judgement. Given the
same equity, price, ATR and config, it returns the same quantity, every time,
and a model has no input to it at any step.

That is the claim the submission makes, so it has to be true structurally
rather than by convention. Hence: a pure function, frozen inputs, no I/O, no
randomness, no clock, and a returned record of which constraint actually bound
so the ledger can show the arithmetic rather than assert it.

The sizing distance deserves a note. The stop TRIGGERS at 1.0x ATR20, but the
API has no resting stop orders, so the exit is submitted on the next polling
cycle and fills at whatever the market is then. Sizing therefore uses 1.5x —
the trigger plus a 0.5x slippage buffer — so that realised loss stays inside
the risk budget even when the software stop is late. Sizing off the trigger
would understate risk by exactly the amount the buffer exists to cover.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Any

from .config import RiskConfig
from .errors import RiskViolation


@dataclass(frozen=True)
class SizingResult:
    """A quantity, and the complete arithmetic behind it."""

    qty: int
    symbol: str
    price: float
    atr20: float

    risk_budget_usd: float
    sizing_distance_usd: float
    qty_from_risk: int
    qty_from_notional_cap: int
    binding_constraint: str

    notional_usd: float
    risk_at_stop_usd: float
    risk_pct_of_equity: float

    expected_edge_usd: float
    round_trip_cost_usd: float
    cost_to_edge_ratio: float

    # Always "deterministic". Present in the ledger so a reader can confirm
    # the field exists and never says "model" — an absent field proves nothing.
    sizing_source: str = "deterministic"

    def to_ledger(self) -> dict[str, Any]:
        return asdict(self)


def size_position(
    cfg: RiskConfig,
    *,
    symbol: str,
    price: float,
    atr20: float,
    equity: float,
) -> SizingResult:
    """Compute quantity. Raises RiskViolation if no valid size exists.

    Raising rather than returning qty=0 is deliberate: a zero quantity is a
    decision with a reason, and the reason has to reach the ledger. Returning
    0 would let a caller treat "this trade cannot clear its costs" as
    indistinguishable from "no signal".
    """
    if symbol not in cfg.symbols:
        raise RiskViolation("SYMBOL_NOT_IN_UNIVERSE", symbol)
    if price <= 0:
        raise RiskViolation("BAD_PRICE", f"{symbol} price={price}")
    if atr20 <= 0:
        raise RiskViolation("BAD_ATR", f"{symbol} atr20={atr20}")
    if equity <= 0:
        raise RiskViolation("BAD_EQUITY", f"equity={equity}")

    sz = cfg.section("sizing")
    fees = cfg.section("fees")

    # --- volatility floor: is this instrument moving enough to be worth trading? ---
    gate = cfg.min_atr20(symbol)
    if atr20 < gate:
        raise RiskViolation(
            "ATR_BELOW_COST_GATE",
            f"{symbol} ATR20 {atr20:.4f} < gate {gate:.4f} — the expected "
            f"move does not clear round-trip cost by the required ratio",
        )

    # --- minimum price ---
    min_price = sz.get("min_price_usd") or cfg.section("universe")["instrument_rules"].get("min_price_usd")
    if min_price and price < float(min_price):
        raise RiskViolation("PRICE_BELOW_FLOOR", f"{symbol} {price} < {min_price}")

    # --- two independent size candidates; the smaller wins ---
    risk_budget = equity * float(sz["risk_per_trade_pct"]) / 100.0
    sizing_distance = float(sz["sizing_risk_distance_atr_mult"]) * atr20
    qty_from_risk = math.floor(risk_budget / sizing_distance)

    notional_cap = equity * float(sz["max_position_notional_pct"]) / 100.0
    qty_from_cap = math.floor(notional_cap / price)

    qty = min(qty_from_risk, qty_from_cap)
    binding = "risk_budget" if qty_from_risk <= qty_from_cap else "notional_cap"

    min_qty = int(sz.get("min_qty", 1))
    if qty < min_qty:
        raise RiskViolation(
            "SIZE_BELOW_MINIMUM",
            f"{symbol} computed qty {qty} < min_qty {min_qty} "
            f"(risk allows {qty_from_risk}, cap allows {qty_from_cap})",
        )

    notional = qty * price
    min_notional = fees.get("min_position_notional_usd")
    if min_notional and notional < float(min_notional):
        raise RiskViolation(
            "NOTIONAL_BELOW_FLOOR",
            f"{symbol} notional ${notional:,.2f} < floor ${float(min_notional):,.2f} "
            f"— fixed commission would dominate the expected edge",
        )

    # --- cost gate, evaluated at the size we would actually trade ---
    # Target is the return to SMA20, which by the entry condition is
    # 1.5 x ATR20 away. Cost is the spread crossed twice plus commission
    # both ways.
    target_distance = float(sz["sizing_risk_distance_atr_mult"]) * atr20
    expected_edge = qty * target_distance
    round_trip_cost = qty * cfg.spread(symbol) + 2 * float(
        fees["commission_per_filled_order_usd"]
    )
    ratio = round_trip_cost / expected_edge if expected_edge else float("inf")
    max_ratio = 1.0 / float(fees["min_expected_edge_to_cost_ratio"])

    if ratio > max_ratio:
        raise RiskViolation(
            "COST_GATE_FAIL",
            f"{symbol} cost ${round_trip_cost:.2f} is {ratio:.1%} of expected "
            f"edge ${expected_edge:.2f}; limit is {max_ratio:.1%}",
        )

    risk_at_stop = qty * sizing_distance

    return SizingResult(
        qty=qty,
        symbol=symbol,
        price=price,
        atr20=atr20,
        risk_budget_usd=round(risk_budget, 4),
        sizing_distance_usd=round(sizing_distance, 6),
        qty_from_risk=qty_from_risk,
        qty_from_notional_cap=qty_from_cap,
        binding_constraint=binding,
        notional_usd=round(notional, 4),
        risk_at_stop_usd=round(risk_at_stop, 4),
        risk_pct_of_equity=round(risk_at_stop / equity * 100.0, 6),
        expected_edge_usd=round(expected_edge, 4),
        round_trip_cost_usd=round(round_trip_cost, 4),
        cost_to_edge_ratio=round(ratio, 6),
    )
