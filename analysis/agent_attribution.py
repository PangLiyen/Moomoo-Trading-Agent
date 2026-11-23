#!/usr/bin/env python3
"""Agent-only return attribution: engine fills only, beside account equity.

25% of the score is the agent's risk-adjusted return. Account 5832647 is not
that number. It also carries the operator's manual trades — the ANT round trip
on 2026-11-20 and the MU share on 2026-11-23 — so headline equity contains P&L
no engine decision produced. Reporting account equity as the agent's return
would credit the agent with someone else's trade, and with the MU mark that is
currently a live, moving number.

WHAT COUNTS AS ENGINE-ORIGINATED, AND WHY THE DEFAULT IS "NO"

A fill is the agent's only if its `order_id` appears on a submission record
(`stage: execute`) the engine wrote. A fill with no submission record is
UNLINKED: the chain has an outcome without a decision, and N34 is exactly what
that costs in attribution — it is reported and counted in neither series. An
order that appears only in the broker's order list is manual, by definition.
The default is therefore exclusion, not inclusion: a trade enters the agent's
return only when the chain shows the engine both decided it and had it filled.

THE SERIES

Cash starts at the configured starting equity. Each engine fill moves cash by
-qty*price - fee (buy) or +qty*price - fee (sell), with the fee taken from the
fill's measured `implied_fee` when present and from the config's cost model
otherwise. Open engine positions are carried at their last fill price, because
the ledger records no marks; realised P&L is the only P&L the chain can prove.

At every account snapshot the report prints account equity, agent equity and
the residual. The residual is everything the engine did not do. It is not
"the manual P&L" — commissions and marks move it too — and is labelled that
way rather than decomposed into a number the ledger cannot support.

Run:  python3 analysis/agent_attribution.py
      python3 analysis/agent_attribution.py --ledger logs/decisions_live.jsonl
Reads the ledger and the config. Writes nothing, places nothing.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.config import RiskConfig  # noqa: E402


def load_records(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def fee_for(rec: dict, default_fee: float) -> tuple[float, str]:
    if "implied_fee" in rec and rec["implied_fee"] is not None:
        return float(rec["implied_fee"]), "measured"
    return default_fee, "config"


def attribute(records: list[dict], start_equity: float,
              default_fee: float) -> dict:
    engine_ids = {str(r["order_id"]) for r in records
                  if r.get("stage") == "execute" and r.get("order_id")}
    manual_ids: set[str] = set()
    for r in records:
        ids = (r.get("inputs") or {}).get("unattributed_order_ids") or []
        manual_ids.update(str(i) for i in ids)

    cash = start_equity
    positions: dict[str, int] = {}
    last_price: dict[str, float] = {}
    rows: list[dict] = []
    engine_fills: list[dict] = []
    unlinked_fills: list[dict] = []

    def equity() -> float:
        return cash + sum(q * last_price.get(s, 0.0) for s, q in positions.items())

    for rec in records:
        stage = rec.get("stage")
        if stage == "fill":
            oid = str(rec.get("order_id", ""))
            entry = {"order_id": oid, "symbol": rec.get("symbol"),
                     "side": rec.get("side"), "qty": rec.get("filled_qty"),
                     "price": rec.get("avg_fill_price"),
                     "time": rec.get("fill_time_et")}
            if rec.get("verdict") not in ("FILLED", "PARTIAL") or not rec.get("filled_qty"):
                entry["note"] = f"verdict {rec.get('verdict')}; no cash effect"
                (engine_fills if oid in engine_ids else unlinked_fills).append(entry)
                continue
            fee, source = fee_for(rec, default_fee)
            entry["fee"], entry["fee_source"] = fee, source
            if oid not in engine_ids:
                unlinked_fills.append(entry)
                continue
            qty = int(rec["filled_qty"])
            price = float(rec["avg_fill_price"])
            sym = str(rec["symbol"])
            if str(rec["side"]).upper() == "BUY":
                cash -= qty * price + fee
                positions[sym] = positions.get(sym, 0) + qty
            else:
                cash += qty * price - fee
                positions[sym] = positions.get(sym, 0) - qty
                if positions[sym] == 0:
                    positions.pop(sym)
                    last_price.pop(sym, None)
            last_price[sym] = price
            engine_fills.append(entry)
        elif stage == "account_snapshot":
            acct_equity = rec.get("total_asset", rec.get("equity"))
            if acct_equity is not None:
                rows.append({
                    "cycle_id": rec.get("cycle_id"),
                    "ts_utc": rec.get("ts_utc"),
                    "account_equity": float(acct_equity),
                    "agent_equity": round(equity(), 6),
                    "residual": round(float(acct_equity) - equity(), 6),
                    "open_engine_positions": dict(positions),
                })

    return {"rows": rows, "engine_fills": engine_fills,
            "unlinked_fills": unlinked_fills,
            "engine_order_ids": sorted(engine_ids),
            "manual_order_ids": sorted(manual_ids),
            "open_positions": dict(positions), "cash": round(cash, 6)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", default="logs/decisions_live.jsonl")
    ap.add_argument("--config", default="config/risk_limits.yaml")
    args = ap.parse_args()

    cfg = RiskConfig.load(ROOT / args.config)
    default_fee = float(cfg.section("fees")["commission_per_filled_order_usd"])
    records = load_records(ROOT / args.ledger)
    res = attribute(records, cfg.starting_equity, default_fee)

    print(f"AGENT-ONLY ATTRIBUTION  ledger {args.ledger}  "
          f"{len(records)} records  start ${cfg.starting_equity:,.2f}")
    print("agent equity = starting equity + engine fills only; open engine "
          "positions carried at last fill price (the ledger has no marks)")
    print(f"\n{'cycle':16s}{'account':>13s}{'agent':>13s}{'residual':>11s}"
          f"  engine positions")
    for r in res["rows"]:
        print(f"{str(r['cycle_id']):16s}{r['account_equity']:13,.2f}"
              f"{r['agent_equity']:13,.2f}{r['residual']:+11,.2f}"
              f"  {r['open_engine_positions'] or 'flat'}")

    print(f"\nengine fills {len(res['engine_fills'])}  "
          f"engine order ids {res['engine_order_ids'] or 'none'}")
    print(f"cash ${res['cash']:,.2f}  open {res['open_positions'] or 'flat'}")
    if res["unlinked_fills"]:
        print(f"UNLINKED fills (no submission record; counted in neither "
              f"series -- N34): {json.dumps(res['unlinked_fills'])}")
    if res["manual_order_ids"]:
        print(f"manual/unattributed order ids seen by the guard, excluded: "
              f"{res['manual_order_ids']}")
    if res["rows"]:
        last = res["rows"][-1]
        print(f"\nlast snapshot {last['cycle_id']}: account "
              f"${last['account_equity']:,.2f}, agent "
              f"${last['agent_equity']:,.2f}, residual "
              f"${last['residual']:+,.2f}. The residual includes manual P&L, "
              f"commissions and marks; it is not decomposed here because the "
              f"ledger cannot support the decomposition.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
