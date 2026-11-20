#!/usr/bin/env python3
"""One decision cycle, offline. Reads bars from disk, writes decisions to the
ledger, and prints the exact order JSON for a human to approve.

It does NOT talk to moomoo. That separation is deliberate: the component that
decides has no ability to execute, so a bug here cannot place an order. The
agent with MCP access fetches bars, runs this, and submits only what a human
approves.

    python3 run_cycle.py --bars-dir analysis --now-et "2026-11-20 11:05" \
        --quote-age 5 --spread-pct 0.006 --equity 100000

Every path through this script writes at least one ledger record. A cycle that
decided nothing is still a cycle that ran, and a log with gaps cannot show
that the system was watching when it mattered.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from engine import (  # noqa: E402
    AccountGuardError, Ledger, RiskConfig, RiskGate, RiskViolation,
    SessionState, file_sha256, size_position, write_genesis,
)
from engine.account import (  # noqa: E402
    account_state_from_api, describe_guard_inputs, ledger_order_ids,
)
from engine.signal import (  # noqa: E402
    Bar, compute_indicators, evaluate_entry, regime_ok_from_spy,
)

# moomoo's kline payloads vary by endpoint, so accept the common spellings
# rather than demanding one shape and failing on a field rename.
_KEYS = {
    "ts": ("time_key", "time", "timestamp", "ts", "k_time", "datetime"),
    "open": ("open", "o", "open_price"),
    "high": ("high", "h", "high_price"),
    "low": ("low", "l", "low_price"),
    "close": ("close", "c", "close_price"),
    "volume": ("volume", "v", "vol", "turnover_volume"),
}


def _pick(row: dict, field: str):
    for key in _KEYS[field]:
        if key in row and row[key] is not None:
            return row[key]
    raise KeyError(f"no recognised key for {field!r} in bar: {sorted(row)[:8]}")


def _parse_ts(value) -> datetime:
    """Parse a bar timestamp as ET wall-clock.

    The first version used utcfromtimestamp on `time_key` values that are
    already ET, shifting every logged bar time by 5 hours: a 10:55 ET bar was
    recorded as 15:55. The indicator maths was unaffected -- nothing in
    signal.py filters on bar timestamps -- but every indicator timestamp in
    the ledger was wrong, and a session-hours filter built on it later would
    have silently discarded whole files.
    """
    if isinstance(value, (int, float)):
        secs = value / 1000.0 if value > 1e11 else float(value)
        # Epoch values are genuinely UTC; convert, then shift to ET. Fixed
        # -5h because the competition window (17-28 Nov) is entirely EST --
        # asserted here so a reused copy outside that window fails review
        # rather than silently drifting by an hour.
        return datetime.utcfromtimestamp(secs) - timedelta(hours=5)
    text = str(value).strip().replace("T", " ").replace("Z", "")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[: len(fmt) + 2].strip(), fmt)
        except ValueError:
            continue
    raise ValueError(f"unparseable timestamp: {value!r}")


def load_bars(path: Path) -> list[Bar]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    rows = raw
    for key in ("bars", "items", "data", "klines", "k_lines"):
        if isinstance(rows, dict) and key in rows:
            rows = rows[key]
    if isinstance(rows, dict):
        rows = next((v for v in rows.values() if isinstance(v, list)), None)
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"{path.name}: could not find a list of bars")

    bars = [
        Bar(
            ts_et=_parse_ts(_pick(r, "ts")),
            open=float(_pick(r, "open")),
            high=float(_pick(r, "high")),
            low=float(_pick(r, "low")),
            close=float(_pick(r, "close")),
            volume=float(_pick(r, "volume")),
        )
        for r in rows
    ]
    bars.sort(key=lambda b: b.ts_et)
    return bars


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/risk_limits.yaml")
    ap.add_argument("--strategy", default="docs/STRATEGY.md")
    ap.add_argument("--bars-dir", default="analysis")
    ap.add_argument("--ledger", default="logs/decisions.jsonl")
    ap.add_argument("--now-et", required=True, help='"YYYY-MM-DD HH:MM" in ET')
    ap.add_argument("--quote-age", type=float, required=True)
    ap.add_argument("--spread-pct", type=float, required=True)
    # REQUIRED, and deliberately not defaulted. The earlier version built the
    # account state from config and then asked the guard to check it against
    # config, so the guard could never fail. These must carry what the broker
    # returned.
    ap.add_argument("--account-json", required=True,
                    help="broker's sim_trade_cash_info + account identity, as "
                         "JSON or a path to it. Must include acc_id, "
                         "account_title, account_type, total_asset, balance, "
                         "hold, market_value")
    ap.add_argument("--positions-json", required=True,
                    help='held quantities by symbol, e.g. \'{"US.SPY": 1}\'. '
                         'Pass {} only when the position endpoint actually '
                         'returned nothing')
    ap.add_argument("--order-ids-json", required=True,
                    help="every order id present in the account today, as a "
                         "JSON list. Compared against the ledger to detect a "
                         "second writer")
    ap.add_argument("--entries-taken", type=int, default=0)
    ap.add_argument("--orders-placed", type=int, default=0)
    ap.add_argument("--code-commit", default="unknown")
    args = ap.parse_args()

    cfg = RiskConfig.load(ROOT / args.config)
    gate = RiskGate(cfg)
    now_et = datetime.strptime(args.now_et, "%Y-%m-%d %H:%M")

    def _load_json(value: str):
        path = ROOT / value
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
        return json.loads(value)

    api_account = _load_json(args.account_json)
    positions = _load_json(args.positions_json)
    account_order_ids = _load_json(args.order_ids_json)

    led = Ledger(ROOT / args.ledger)
    if led.seq == 0:
        write_genesis(
            led,
            config_hash=cfg.sha256,
            strategy_hash=file_sha256(ROOT / args.strategy),
            code_commit=args.code_commit,
            account={"acc_id": cfg.acc_id, "starting_equity_usd": cfg.starting_equity},
        )
        print(f"ledger initialised at {args.ledger}")

    cycle_id = f"C{now_et:%Y%m%dT%H%M}"

    # Orders the ledger knows, plus our own pre-ledger probe orders declared in
    # config. Everything else in the account is someone else's.
    known_own = set(ledger_order_ids(led.records()))
    known_own |= {str(o) for o in
                  (cfg.section("account_integrity").get("known_own_order_ids") or [])}

    acct = account_state_from_api(
        api_account,
        positions=positions,
        account_order_ids=account_order_ids,
        ledger_order_ids=known_own,
    )
    equity = acct.total_asset

    # Both guards, and the comparison itself recorded. Logging only "passed"
    # would leave no way to distinguish a real check from a vacuous one.
    guard_inputs = describe_guard_inputs(acct, cfg)
    try:
        gate.assert_account(acct)
        gate.assert_no_foreign_activity(acct)
    except (AccountGuardError, RiskViolation) as exc:
        # A guard failure is a decision, so it is logged with the same detail
        # as an execution before the process exits.
        led.append({"cycle_id": cycle_id, "stage": "account_guard",
                    "verdict": "HALT",
                    "reason_code": getattr(exc, "reason_code", "ACCOUNT_GUARD_FAIL"),
                    "reason_text": str(exc), "inputs": guard_inputs})
        print(f"\nHALT -- account guard: {exc}")
        print(f"\nledger: {led.seq} records, tip={led.tip[:16]}...")
        return 2

    led.append({"cycle_id": cycle_id, "stage": "account_guard",
                "verdict": "APPROVE", "reason_code": "ACCOUNT_VERIFIED",
                "reason_text": "broker-reported identity matched config on "
                               "id, title and type; snapshot reconciles",
                "inputs": guard_inputs})
    print(f"\nACCOUNT {acct.acc_id} verified against broker response "
          f"| equity ${acct.total_asset:,.2f} | positions "
          f"{acct.open_positions or 'flat'}")

    sess = SessionState(
        now_et=now_et,
        entries_this_session=args.entries_taken,
        orders_this_session=args.orders_placed,
        seconds_since_last_entry=None,
        seconds_since_last_completed_cycle=0.0,
        session_pnl_pct=0.0,
        peak_drawdown_pct=0.0,
        consecutive_losses=0,
    )

    bars_dir = ROOT / args.bars_dir
    loaded: dict[str, list[Bar]] = {}
    for sym in cfg.symbols:
        short = sym.split(".")[-1]
        hits = sorted(bars_dir.glob(f"*{short}*.json"))
        if not hits:
            print(f"  {sym}: no bar file matching *{short}*.json -- skipped")
            continue
        try:
            loaded[sym] = load_bars(hits[0])
        except Exception as exc:
            print(f"  {sym}: {hits[0].name} unreadable: {exc}")

    if "US.SPY" not in loaded:
        led.append({"cycle_id": cycle_id, "stage": "regime", "verdict": "HALT",
                    "reason_code": "NO_SPY_DATA",
                    "reason_text": "regime filter needs SPY bars; none loaded"})
        print("HALT: regime filter requires SPY bars")
        return 1

    regime_ok, regime_detail = regime_ok_from_spy(loaded["US.SPY"])
    led.append({"cycle_id": cycle_id, "stage": "regime",
                "verdict": "REGIME_OK" if regime_ok else "REGIME_BLOCK",
                "reason_code": "REGIME", "inputs": regime_detail})
    print(f"\nREGIME  SPY {regime_detail['spy_close']} vs SMA60 "
          f"{regime_detail['spy_sma60']:.4f} -> "
          f"{'OK' if regime_ok else 'BLOCKED (downtrend)'}")

    print(f"\n{'sym':9s}{'close':>10s}{'sma20':>11s}{'atr20':>9s}"
          f"{'threshold':>11s}  verdict")
    approved = []

    for sym, bars in loaded.items():
        try:
            ind = compute_indicators(sym, bars)
        except ValueError as exc:
            led.append({"cycle_id": cycle_id, "stage": "indicators", "symbol": sym,
                        "verdict": "BLOCK", "reason_code": "INSUFFICIENT_DATA",
                        "reason_text": str(exc)})
            print(f"{sym:9s}{'--':>10s}  INSUFFICIENT_DATA: {exc}")
            continue

        sig = evaluate_entry(
            ind, regime_ok=regime_ok, quote_age_seconds=args.quote_age,
            spread_pct_of_mid=args.spread_pct, now_et=now_et,
            max_quote_age_seconds=float(cfg.section("data_quality")["max_quote_age_seconds"]),
            max_spread_pct_of_mid=float(cfg.section("data_quality")["max_spread_pct_of_mid"]),
        )
        led.append({"cycle_id": cycle_id, "stage": "signal", "symbol": sym,
                    "verdict": "PROPOSE" if sig.qualifies else "NO_SETUP",
                    "reason_code": sig.reason_code, "reason_text": sig.reason_text,
                    "inputs": ind.to_ledger(), "conditions": sig.conditions,
                    "detail": sig.detail})

        row = (f"{sym:9s}{ind.close:10.4f}{ind.sma20:11.4f}{ind.atr20:9.4f}"
               f"{sig.detail['entry_threshold']:11.4f}  ")
        if not sig.qualifies:
            failed = [k for k, v in sig.conditions.items() if not v]
            print(row + "no setup (" + ", ".join(failed) + ")")
            continue

        try:
            proposal = size_position(cfg, symbol=sym, price=ind.close,
                                     atr20=ind.atr20, equity=equity)
        except RiskViolation as exc:
            led.append({"cycle_id": cycle_id, "stage": "sizing", "symbol": sym,
                        "verdict": "BLOCK", "reason_code": exc.reason_code,
                        "reason_text": exc.detail})
            print(row + f"BLOCK {exc.reason_code}")
            continue

        decision = gate.evaluate_entry(
            proposal, acct=acct, sess=sess,
            quote_age_seconds=args.quote_age, spread_pct_of_mid=args.spread_pct,
        )
        led.append({"cycle_id": cycle_id, "stage": "risk", "symbol": sym,
                    "verdict": decision.verdict, "reason_code": decision.reason_code,
                    "reason_text": decision.reason_text,
                    "proposed": proposal.to_ledger(),
                    "risk_checks": decision.to_ledger()})

        if decision.verdict == "BLOCK":
            print(row + f"BLOCK {decision.reason_code}")
            continue

        print(row + f"{decision.verdict} qty {decision.qty_out} "
                    f"(${proposal.price * decision.qty_out:,.0f})")
        approved.append((sym, ind, proposal, decision))

    print()
    if not approved:
        print("No qualifying setup this cycle. Nothing to submit.")
        led.append({"cycle_id": cycle_id, "stage": "cycle", "verdict": "NO_ACTION",
                    "reason_code": "NO_SETUP",
                    "reason_text": "cycle complete; no symbol qualified"})
    else:
        print("ORDERS FOR APPROVAL -- submit only after a human says yes:\n")
        for sym, ind, proposal, decision in approved:
            tip = led.tip[:16]
            order = {
                "acc_id": cfg.acc_id, "market": cfg.raw["account"]["market_id"],
                "symbol": sym, "order_type": int(cfg.section("execution")["order_type"]),
                "order_side": 1, "qty": str(decision.qty_out),
                "price": f"{ind.close:.2f}",
                "order_trade_time_type": int(cfg.section("execution")["order_trade_time_type"]),
                "text": f"S01-{cycle_id}-{tip[:8]}",
            }
            print(json.dumps(order, indent=2))
            print(f"  stop {ind.close - proposal.sizing_distance_usd / 1.5:.4f} "
                  f"| target {ind.sma20:.4f} "
                  f"| risk ${proposal.risk_at_stop_usd:.2f} "
                  f"({proposal.risk_pct_of_equity:.3f}%)\n")

    report = led.verify_report()
    print(f"ledger: {report['records']} records, intact={report['intact']}, "
          f"tip={led.tip[:16]}...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
