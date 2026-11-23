#!/usr/bin/env python3
"""One decision cycle. Decides and records; it cannot execute.

    python3 run_cycle.py \
        --now-et "2026-11-23 10:35" \
        --account-json acct.json \
        --positions-json '{"US.SPY": 20}' \
        --order-ids-json orders.json \
        --quotes-json quotes.json \
        --bars-dir analysis \
        [--fills-json fills.json] \
        [--require-clean-tree]

There is no network call anywhere in this file or in anything it imports from
`engine/`. It reads bars and broker figures from disk, writes decisions to the
ledger, and emits an intents file for something with credentials to submit.
A defect here cannot reach the broker.

ORDER OF OPERATIONS, AND WHY

    1. provenance        a cycle whose code cannot be identified writes no evidence
    2. session plan      a closed day or a half day is resolved from config
    3. fills ingested    yesterday's outcomes before today's decisions
    4. bars validated    RTH only, interval checked, staleness refused
    5. state rebuilt     from the ledger, cross-checked against the broker
    6. snapshot written  equity, peak, drawdown -- every cycle
    7. account guard     broker identity vs config, and foreign-order halt
    8. EXITS             existing positions before new ones
    9. entries           only if exits left room and every gate passed
   10. intents written

Exits precede entries deliberately. A cycle that opens a position while an
existing one needs stopping out has its priorities backwards, and with
software-only stops the open position is the one with no protection.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from engine import (  # noqa: E402
    AccountGuardError, Ledger, RiskConfig, RiskGate, RiskViolation,
    StaleDataError, account_state_from_api, assert_order_list_fresh,
    assert_quote_fresh, build_order, crossing_limit, describe_code,
    describe_guard_inputs, distance_to_trigger_atr, engine_file_set_hash,
    epoch_ms_to_et, file_sha256, last_pinned_commit, ledger_order_ids,
    plan_code_change, plan_exits, plan_session, record_account_snapshot,
    record_exit, record_fill, record_submission, reconstruct, size_position,
    sort_by_distance, split_positions, validate_bars, write_intents,
    write_genesis,
)
from engine.signal import (  # noqa: E402
    Bar, compute_indicators, evaluate_entry, regime_ok_from_spy,
)

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
    """Bar timestamps are ET wall-clock.

    Numeric `time_key` values convert through engine.timeutil, the single
    epoch -> ET helper. This function did its own arithmetic once and shifted
    every logged bar time by five hours; there is no second conversion path
    left here to drift.
    """
    if isinstance(value, (int, float)):
        return epoch_ms_to_et(value)
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
        Bar(ts_et=_parse_ts(_pick(r, "ts")), open=float(_pick(r, "open")),
            high=float(_pick(r, "high")), low=float(_pick(r, "low")),
            close=float(_pick(r, "close")), volume=float(_pick(r, "volume")))
        for r in rows
    ]
    bars.sort(key=lambda b: b.ts_et)
    return bars


def _load_json(value: str, root: Path = ROOT):
    p = root / value
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return json.loads(value)


def _newest_bar_file(bars_dir: Path, short: str) -> Path | None:
    """Newest matching file, not the alphabetically first.

    `sorted(glob)[0]` let a stale file win over today's, which combined with
    unenforced RTH filtering meant stale bars could drive a live order.
    """
    hits = sorted(bars_dir.glob(f"*{short}*.json"),
                  key=lambda p: p.stat().st_mtime, reverse=True)
    return hits[0] if hits else None


def _et_now_arg(value: str) -> datetime:
    """--now-et must carry seconds (N32).

    A minute-resolution clock quantises every age the driver derives from it
    -- bar staleness, the order-list age -- and can understate one by up to
    59s against a 120s bound. That is exactly enough for a stale snapshot or
    an old bar to pass. The value is rejected at the parse, before the ledger
    is touched, rather than rounded.
    """
    try:
        return datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"--now-et must be 'YYYY-MM-DD HH:MM:SS' in ET, with seconds; got "
            f"{value!r}. A minute-resolution value understates every age the "
            f"cycle measures by up to 59s against its bound (N32)."
        )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config/risk_limits.yaml")
    ap.add_argument("--strategy", default="docs/STRATEGY.md")
    ap.add_argument("--bars-dir", default="analysis")
    ap.add_argument("--ledger", default="logs/decisions.jsonl")
    ap.add_argument("--intents-out", default="outbox")
    ap.add_argument("--now-et", required=True, type=_et_now_arg,
                    help='"YYYY-MM-DD HH:MM:SS" ET -- seconds are required')
    ap.add_argument("--account-json", required=True)
    ap.add_argument("--positions-json", required=True)
    ap.add_argument("--order-ids-json", required=True)
    ap.add_argument("--quotes-json", required=True,
                    help="per-symbol {bid, ask, book_age_seconds}")
    ap.add_argument("--fills-json", default=None,
                    help="fill reports to ingest before deciding")
    ap.add_argument("--max-bar-age-minutes", type=float, default=30.0)
    ap.add_argument("--require-clean-tree", action="store_true")
    args = ap.parse_args()

    cfg = RiskConfig.load(ROOT / args.config)
    gate = RiskGate(cfg)
    now_et = args.now_et
    code = describe_code(ROOT, require_clean=args.require_clean_tree)

    led = Ledger(ROOT / args.ledger)
    if led.seq == 0:
        write_genesis(led, config_hash=cfg.sha256,
                      strategy_hash=file_sha256(ROOT / args.strategy),
                      code_commit=code["code_commit"],
                      account={"acc_id": cfg.acc_id,
                               "starting_equity_usd": cfg.starting_equity})
        led.append({"stage": "genesis_provenance", "verdict": "INIT",
                    "reason_code": "CODE_VERIFIED", "provenance": code,
                    "reason_text": "commit derived from git and verified to "
                                   "contain every engine file"})
        print(f"ledger initialised  code {code['code_commit_short']}  "
              f"rules {cfg.sha256[:12]}")
    if code["engine_files_dirty"]:
        print(f"WARNING engine modified on disk: {code['engine_files_dirty']}")

    cycle_id = f"C{now_et:%Y%m%dT%H%M}"

    # A cycle record pins its own commit, so an engine change mid-chain is
    # visible inside the chain instead of only to someone diffing commits.
    # Written before any record for this cycle.
    previous_pin = last_pinned_commit(led.records())
    change = plan_code_change(
        led.records(),
        current_commit=code["code_commit"],
        current_file_set_hash=engine_file_set_hash(ROOT, code["code_commit"]),
        previous_file_set_hash=(
            engine_file_set_hash(ROOT, previous_pin) if previous_pin else None),
    )
    if change:
        change["cycle_id"] = cycle_id
        led.append(change)
        print(f"CODE CHANGE  {previous_pin[:12]} -> "
              f"{code['code_commit_short']}  recorded in chain")

    plan = plan_session(cfg, now_et)
    led.append({"cycle_id": cycle_id, "stage": "session_plan",
                "verdict": "OBSERVED", "reason_code": "SESSION_RESOLVED",
                "reason_text": plan.note or f"{plan.status} session",
                "inputs": plan.to_ledger()})
    print(f"\nSESSION {plan.trading_date} {plan.status}  "
          f"entry {plan.earliest_entry_et:%H:%M}-{plan.latest_entry_et:%H:%M}  "
          f"flat by {plan.flat_by_et:%H:%M}")

    if not plan.is_trading_day:
        led.append({"cycle_id": cycle_id, "stage": "cycle", "verdict": "HALT",
                    "reason_code": "NOT_A_TRADING_DAY",
                    "reason_text": f"{plan.trading_date} is {plan.status}: "
                                   f"{plan.note}",
                    "code_commit": code["code_commit"]})
        print(f"HALT  {plan.trading_date} is {plan.status}. {plan.note}")
        return 0

    # ---- 3. fills first: yesterday's outcomes before today's decisions ----
    if args.fills_json:
        for f in _load_json(args.fills_json):
            record_fill(led, cycle_id=cycle_id, order_id=str(f["order_id"]),
                        symbol=f["symbol"], side=f["side"],
                        verdict=f.get("verdict", "FILLED"),
                        filled_qty=int(f.get("filled_qty", 0)),
                        avg_fill_price=float(f.get("avg_fill_price", 0)),
                        fill_time_et=f.get("fill_time_et", now_et.isoformat()),
                        limit_price=f.get("limit_price"),
                        cash_before=f.get("cash_before"),
                        cash_after=f.get("cash_after"),
                        submitted_at_et=f.get("submitted_at_et"))
        print(f"ingested {len(_load_json(args.fills_json))} fill report(s)")

    # ---- 4. bars ----
    quotes = _load_json(args.quotes_json)
    bars_dir = ROOT / args.bars_dir
    indicators: dict[str, Any] = {}
    audits: dict[str, Any] = {}
    raw_bars: dict[str, list[Bar]] = {}

    for sym in cfg.symbols:
        path = _newest_bar_file(bars_dir, sym.split(".")[-1])
        if path is None:
            print(f"  {sym}: no bar file -- skipped")
            continue
        try:
            audit = validate_bars(
                sym, load_bars(path), now_et=now_et,
                max_staleness_seconds=args.max_bar_age_minutes * 60,
            )
        except (StaleDataError, ValueError) as exc:
            led.append({"cycle_id": cycle_id, "stage": "data_quality",
                        "symbol": sym, "verdict": "BLOCK",
                        "reason_code": "STALE_OR_INVALID_BARS",
                        "reason_text": str(exc), "source_file": path.name})
            print(f"  {sym}: {exc}")   # bars.py no longer repeats the symbol
            continue
        audits[sym], raw_bars[sym] = audit, list(audit.kept)

    led.append({"cycle_id": cycle_id, "stage": "data_quality",
                "verdict": "OBSERVED", "reason_code": "BARS_VALIDATED",
                "reason_text": "RTH filter, interval check and staleness gate",
                "inputs": {s: a.to_ledger() for s, a in audits.items()}})

    for sym, bars in raw_bars.items():
        try:
            indicators[sym] = compute_indicators(sym, bars)
        except ValueError as exc:
            led.append({"cycle_id": cycle_id, "stage": "indicators",
                        "symbol": sym, "verdict": "BLOCK",
                        "reason_code": "INSUFFICIENT_DATA",
                        "reason_text": str(exc)})
            print(f"  {sym}: INSUFFICIENT_DATA: {exc}")

    # ---- 5. state, cross-checked ----
    api_account = _load_json(args.account_json)
    position_rows = _load_json(args.positions_json)
    orders_input = _load_json(args.order_ids_json)

    # The order list's capture time travels with the orders, rather than being
    # retyped on the command line. The guard's answer is only as current as
    # the snapshot it reads (N31), so an untimed list is unusable input.
    try:
        if not (isinstance(orders_input, dict)
                and "captured_at_et" in orders_input
                and "order_ids" in orders_input):
            raise ValueError(
                'order list input must be {"captured_at_et": '
                '"YYYY-MM-DD HH:MM:SS", "order_ids": [...]}. A bare list '
                'carries no capture time, so the foreign-order guard cannot '
                'bound the age of the state it attests to (N31).'
            )
        order_list_captured_at = _parse_ts(orders_input["captured_at_et"])
        account_order_ids = [str(o) for o in orders_input["order_ids"]]
        broker_positions, foreign_market_values = split_positions(
            position_rows, cfg.symbols)
    except (AccountGuardError, ValueError, KeyError, TypeError) as exc:
        led.append({"cycle_id": cycle_id, "stage": "account_guard",
                    "verdict": "HALT", "reason_code": "GUARD_INPUT_INVALID",
                    "reason_text": str(exc)})
        led.append({"cycle_id": cycle_id, "stage": "cycle", "verdict": "HALT",
                    "reason_code": "GUARD_INPUT_INVALID",
                    "reason_text": "cycle halted before the account guard: an "
                                   "input it attests to is unusable",
                    "code_commit": code["code_commit"]})
        print(f"\nHALT -- guard input invalid: {exc}")
        return 2

    known_own = set(ledger_order_ids(led.records()))
    known_own |= {str(o) for o in
                  (cfg.section("account_integrity").get("known_own_order_ids") or [])}
    acct = account_state_from_api(api_account, positions=broker_positions,
                                  account_order_ids=account_order_ids,
                                  ledger_order_ids=known_own)

    halted = os.environ.get(str(cfg.section("kill_switch")["manual_halt_env_var"]),
                            "").lower() == "halt"
    live = reconstruct(led.records(), cfg, now_et=now_et,
                       broker_equity=acct.total_asset,
                       broker_positions=broker_positions,
                       foreign_market_values=foreign_market_values,
                       halted=halted,
                       halt_reason="AGENT_MODE=halt" if halted else "")

    record_account_snapshot(led, cycle_id=cycle_id, live_state=live)
    sess = live.session_state
    print(f"EQUITY ${live.equity:,.2f}  sizing basis ${live.sizing_equity:,.2f}  "
          f"peak ${live.peak_equity:,.2f}  "
          f"dd {sess.peak_drawdown_pct:.3f}%  session {sess.session_pnl_pct:+.3f}%")
    if live.foreign_positions:
        print(f"FOREIGN {live.foreign_positions}  "
              f"mv ${live.foreign_market_value:,.2f} excluded from the sizing basis")
    print(f"STATE  positions {live.open_quantities() or 'flat'}  "
          f"entries today {sess.entries_this_session}  "
          f"losses in a row {sess.consecutive_losses}  "
          f"cycle gap {sess.seconds_since_last_completed_cycle:.0f}s")
    for w in live.warnings:
        print(f"  WARNING {w}")

    # A mismatch warning says the two sources disagree; it does not say whether
    # the disagreement is the expected one. A difference fully accounted for by
    # positions declared foreign is normal output of a shared account. A
    # difference anywhere else is a finding, and the two must not read alike.
    derived_positions = live.open_quantities()
    broker_only = {s: q for s, q in broker_positions.items()
                   if int(q) != derived_positions.get(s, 0)}
    ledger_only = {s: q for s, q in derived_positions.items()
                   if broker_positions.get(s, 0) != q}
    if broker_only or ledger_only:
        declared = set(live.foreign_positions)
        unexplained = {s: q for s, q in broker_only.items() if s not in declared}
        if unexplained or ledger_only:
            verdict, text = "UNEXPLAINED", (
                f"broker-only {unexplained or '{}'}, ledger-only "
                f"{ledger_only or '{}'} -- a difference at a symbol not "
                f"declared foreign is a finding, not an expectation")
            print(f"  POSITION MISMATCH {verdict}: {text}")
        else:
            verdict, text = "EXPLAINED", (
                f"every difference is a declared foreign position "
                f"{sorted(declared)} outside the universe")
            print(f"  POSITION MISMATCH {verdict}: {text}")
        led.append({"cycle_id": cycle_id, "stage": "position_check",
                    "verdict": verdict, "reason_code": "POSITION_MISMATCH",
                    "reason_text": text,
                    "broker": broker_positions,
                    "ledger_derived": derived_positions,
                    "declared_foreign": sorted(declared)})

    # ---- 7. guards ----
    guard_inputs = describe_guard_inputs(acct, cfg)
    max_order_age = float(
        cfg.section("account_integrity")["max_order_list_age_seconds"])
    order_age = (now_et - order_list_captured_at).total_seconds()
    guard_inputs["order_list"] = {
        "captured_at_et": order_list_captured_at.isoformat(sep=" "),
        "age_seconds": round(order_age, 3),
        "max_age_seconds": max_order_age,
    }
    try:
        # Freshness first: a guard cannot honestly attest to a state older
        # than its bound, so the stale snapshot is refused before the
        # comparison rather than after it.
        assert_order_list_fresh(order_age, max_order_age)
        gate.assert_account(acct)
        gate.assert_no_foreign_activity(acct)
    except StaleDataError as exc:
        led.append({"cycle_id": cycle_id, "stage": "account_guard",
                    "verdict": "HALT", "reason_code": "STALE_ORDER_LIST",
                    "reason_text": str(exc), "inputs": guard_inputs})
        led.append({"cycle_id": cycle_id, "stage": "cycle", "verdict": "HALT",
                    "reason_code": "STALE_ORDER_LIST",
                    "reason_text": "cycle halted at the account guard: the "
                                   "order list is too old to attest to",
                    "code_commit": code["code_commit"]})
        print(f"\nHALT -- stale order list: {exc}")
        return 2
    except (AccountGuardError, RiskViolation) as exc:
        led.append({"cycle_id": cycle_id, "stage": "account_guard",
                    "verdict": "HALT",
                    "reason_code": getattr(exc, "reason_code", "ACCOUNT_GUARD_FAIL"),
                    "reason_text": str(exc), "inputs": guard_inputs})
        led.append({"cycle_id": cycle_id, "stage": "cycle", "verdict": "HALT",
                    "reason_code": getattr(exc, "reason_code", "ACCOUNT_GUARD_FAIL"),
                    "reason_text": "cycle halted at the account guard",
                    "code_commit": code["code_commit"]})
        print(f"\nHALT -- account guard: {exc}")
        return 2
    led.append({"cycle_id": cycle_id, "stage": "account_guard",
                "verdict": "APPROVE", "reason_code": "ACCOUNT_VERIFIED",
                "reason_text": "broker identity matched config on id, title "
                               "and type; snapshot reconciles",
                "inputs": guard_inputs, "rules_hash": cfg.sha256,
                "code_commit": code["code_commit"],
                "engine_files_dirty": code["engine_files_dirty"]})
    # The age is printed on the pass path, not only when it refuses: the guard
    # record already carries it, and a bound only visible on failure reads as
    # decoration.
    print(f"GUARD  order list captured {order_list_captured_at:%H:%M:%S} ET, "
          f"age {order_age:.0f}s of {max_order_age:.0f}s bound; account id, "
          f"title and type verified")

    orders: list[dict[str, Any]] = []

    # ---- 8. EXITS, before any entry ----
    exit_intents, exit_verdicts = plan_exits(
        live.positions, indicators, cfg, now_et=now_et, plan=plan)
    for v in exit_verdicts:
        led.append({"cycle_id": cycle_id, "stage": "exit_check",
                    "symbol": v.symbol,
                    "verdict": "EXIT_SIGNAL" if v.qualifies else "HOLD",
                    "reason_code": v.reason_code, "reason_text": v.reason_text,
                    "detail": v.detail})

    for intent in exit_intents:
        q = quotes.get(intent.symbol, {})
        bid, ask = float(q.get("bid") or 0), float(q.get("ask") or 0)
        if bid <= 0 or ask <= 0:
            led.append({"cycle_id": cycle_id, "stage": "exit_check",
                        "symbol": intent.symbol, "verdict": "ESCALATE",
                        "reason_code": "NO_QUOTE_FOR_EXIT",
                        "reason_text": "an exit is required and there is no "
                                       "quote to price it; needs a human"})
            print(f"  ESCALATE {intent.symbol}: exit required, no quote")
            continue
        price = crossing_limit(cfg, side="SELL", bid=bid, ask=ask,
                               urgency=intent.urgency)
        order = build_order(cfg, symbol=intent.symbol, side="SELL",
                            qty=intent.qty, limit_price=price,
                            cycle_id=cycle_id, ledger_tip=led.tip)
        record_submission(led, cycle_id=cycle_id, order=order, intent="EXIT",
                          approved_qty=intent.qty, exit_intent=intent)
        orders.append(order)
        print(f"  EXIT {intent.symbol} {intent.qty} @ {price:.2f} "
              f"[{intent.exit_rule}, {intent.urgency}]")

    # ---- 9. entries ----
    held_after_exits = dict(live.open_quantities())
    for intent in exit_intents:
        held_after_exits.pop(intent.symbol, None)

    if "US.SPY" not in indicators:
        led.append({"cycle_id": cycle_id, "stage": "regime", "verdict": "BLOCK",
                    "reason_code": "NO_SPY_DATA",
                    "reason_text": "the regime filter needs SPY bars"})
        print("\nno SPY data: entries blocked")
        regime_ok = False
    else:
        regime_ok, regime_detail = regime_ok_from_spy(raw_bars["US.SPY"])
        led.append({"cycle_id": cycle_id, "stage": "regime",
                    "verdict": "REGIME_OK" if regime_ok else "REGIME_BLOCK",
                    "reason_code": "REGIME", "inputs": regime_detail})
        print(f"\nREGIME SPY {regime_detail['spy_close']} vs SMA60 "
              f"{regime_detail['spy_sma60']:.4f} -> "
              f"{'OK' if regime_ok else 'BLOCKED'}")

    from dataclasses import replace as _replace
    acct_for_entries = _replace(acct, open_positions=held_after_exits)

    max_age = float(cfg.section("data_quality")["max_quote_age_seconds"])
    if cfg.observe_only:
        print(f"\nOBSERVE-ONLY  entries disabled: {cfg.observe_only_reason}")
    display: list[dict[str, Any]] = []
    for sym, ind in indicators.items():
        q = quotes.get(sym, {})
        age = float(q.get("book_age_seconds", 1e9))
        try:
            assert_quote_fresh(age, max_age)
        except StaleDataError as exc:
            negative = age < 0
            led.append({"cycle_id": cycle_id, "stage": "data_quality",
                        "symbol": sym,
                        "verdict": "HALT" if negative else "BLOCK",
                        "reason_code": ("NEGATIVE_QUOTE_AGE" if negative
                                        else "STALE_QUOTE"),
                        "reason_text": str(exc),
                        "quote_age_seconds": age})
            print(f"  {sym}: {exc}")
            if negative:
                led.append({"cycle_id": cycle_id, "stage": "cycle",
                            "verdict": "HALT",
                            "reason_code": "NEGATIVE_QUOTE_AGE",
                            "reason_text": "quote age is negative: the cycle "
                                           "cannot date its data and stops",
                            "code_commit": code["code_commit"]})
                return 2
            continue
        mid = (float(q.get("bid") or 0) + float(q.get("ask") or 0)) / 2
        spread_pct = (
            (float(q["ask"]) - float(q["bid"])) / mid * 100.0
            if mid > 0 and q.get("ask") and q.get("bid") else 1e9
        )
        ask = float(q.get("ask") or 0)

        sig = evaluate_entry(
            ind, regime_ok=regime_ok, live_ask=ask,
            quote_age_seconds=age,
            spread_pct_of_mid=spread_pct, now_et=now_et,
            max_quote_age_seconds=max_age,
            max_spread_pct_of_mid=float(cfg.section("data_quality")["max_spread_pct_of_mid"]),
            entry_window=(plan.earliest_entry_et, plan.latest_entry_et),
        )
        led.append({"cycle_id": cycle_id, "stage": "signal", "symbol": sym,
                    "verdict": "PROPOSE" if sig.qualifies else "NO_SETUP",
                    "reason_code": sig.reason_code,
                    "reason_text": sig.reason_text, "inputs": ind.to_ledger(),
                    "conditions": sig.conditions, "detail": sig.detail})

        row = {"symbol": sym, "close": ind.close, "sma20": ind.sma20,
               "atr20": ind.atr20,
               "threshold": sig.detail["entry_threshold"],
               "distance_atr": distance_to_trigger_atr(
                   ind.close, sig.detail["entry_threshold"], ind.atr20)}
        if not sig.qualifies:
            row["action"] = ("no setup (" +
                             ", ".join(k for k, v in sig.conditions.items()
                                       if not v) + ")")
            display.append(row)
            continue

        if cfg.observe_only:
            # Refuse after indicators are computed, so the record still shows
            # what the signal was that would have been taken. This blocks
            # entering only; exits above were already planned and submitted.
            led.append({"cycle_id": cycle_id, "stage": "risk", "symbol": sym,
                        "verdict": "BLOCK", "reason_code": "OBSERVE_ONLY",
                        "reason_text": f"entry refused: {cfg.observe_only_reason}",
                        "observed_signal": {"conditions": sig.conditions,
                                            "detail": sig.detail},
                        "inputs": ind.to_ledger()})
            row["action"] = "BLOCK OBSERVE_ONLY"
            display.append(row)
            continue

        try:
            proposal = size_position(cfg, symbol=sym, price=ind.close,
                                     atr20=ind.atr20, equity=live.sizing_equity)
        except RiskViolation as exc:
            led.append({"cycle_id": cycle_id, "stage": "sizing", "symbol": sym,
                        "verdict": "BLOCK", "reason_code": exc.reason_code,
                        "reason_text": exc.detail})
            row["action"] = f"BLOCK {exc.reason_code}"
            display.append(row)
            continue

        decision = gate.evaluate_entry(proposal, acct=acct_for_entries,
                                       sess=sess, quote_age_seconds=age,
                                       spread_pct_of_mid=spread_pct)
        led.append({"cycle_id": cycle_id, "stage": "risk", "symbol": sym,
                    "verdict": decision.verdict,
                    "reason_code": decision.reason_code,
                    "reason_text": decision.reason_text,
                    "proposed": proposal.to_ledger(),
                    "risk_checks": decision.to_ledger()})
        if decision.verdict == "BLOCK":
            row["action"] = f"BLOCK {decision.reason_code}"
            display.append(row)
            continue

        price = crossing_limit(cfg, side="BUY", bid=float(q["bid"]),
                               ask=float(q["ask"]), urgency="normal")
        order = build_order(cfg, symbol=sym, side="BUY",
                            qty=decision.qty_out, limit_price=price,
                            cycle_id=cycle_id, ledger_tip=led.tip)
        record_submission(led, cycle_id=cycle_id, order=order, intent="ENTRY",
                          approved_qty=decision.qty_out,
                          proposal=proposal.to_ledger())
        orders.append(order)
        held_after_exits[sym] = decision.qty_out
        acct_for_entries = _replace(acct, open_positions=held_after_exits)
        row["action"] = f"{decision.verdict} qty {decision.qty_out} @ {price:.2f}"
        display.append(row)

    print(f"\n{'sym':9s}{'close':>10s}{'sma20':>11s}{'atr20':>9s}"
          f"{'threshold':>11s}{'d_atr':>8s}  verdict")
    for r in sort_by_distance(display):
        print(f"{r['symbol']:9s}{r['close']:10.4f}{r['sma20']:11.4f}"
              f"{r['atr20']:9.4f}{r['threshold']:11.4f}"
              f"{r['distance_atr']:8.2f}  {r['action']}")

    # ---- 10. intents ----
    led.append({"cycle_id": cycle_id, "stage": "cycle",
                "verdict": "ORDERS_PENDING" if orders else "NO_ACTION",
                "reason_code": "CYCLE_COMPLETE",
                "reason_text": f"{len(orders)} order(s) awaiting submission"
                               if orders else "cycle complete; no action",
                "execution_mode": ("observe_only" if cfg.observe_only
                                   else "normal"),
                "code_commit": code["code_commit"]})

    if orders:
        out_dir = Path(args.intents_out)
        if not out_dir.is_absolute():
            out_dir = ROOT / out_dir
        p = write_intents(out_dir / f"intents_{cycle_id}.json", cycle_id, orders)
        try:
            shown = p.relative_to(ROOT)
        except ValueError:
            shown = p
        print(f"\n{len(orders)} order(s) written to {shown}")
        print("SUBMIT ONLY AFTER HUMAN APPROVAL. This process cannot submit.")
    else:
        print("\nNo action this cycle.")

    report = led.verify_report()
    print(f"ledger: {report['records']} records, intact={report['intact']}, "
          f"tip={led.tip[:16]}...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
