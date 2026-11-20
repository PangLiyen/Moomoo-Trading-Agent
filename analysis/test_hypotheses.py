#!/usr/bin/env python3
"""Four pre-registered hypotheses, each reported on TRAIN and TEST separately.

WHY THIS FILE EXISTS

The invalidation of the mean-reversion rule leaves one question a failed
backtest cannot answer: was the loss the entry rule, or the exit structure,
or noise? Four hypotheses were fixed in writing before any number was
computed. They are printed at the top of this script's output, ahead of every
result, so the output itself shows the set was closed in advance.

Any result below is a measurement on 9 sessions of 5-minute bars. It is not
parameter selection: no threshold, stop, target or filter anywhere in config/
or engine/ is changed by anything here, and no result here is used to choose
one. If none of the four works, that is the answer.

DEFINITIONS are imported from analysis/compute_indicators.py (bar cleaning,
Wilder ATR20, SMA20, entry window) and analysis/measure_signal_rate.py
(merged archive+live loader, SPY SMA60 regime, 60-min time stop, 15:50 flat,
$1.99 round trip). Nothing is re-derived here except the exits that H1/H2
need, which are stated in the fixed header.

Run:  python3 analysis/test_hypotheses.py
Also writes: analysis/test_hypotheses_output.json
"""

from __future__ import annotations

import json
import random
import statistics
import sys
from datetime import datetime
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

import compute_indicators as ci  # noqa: E402
import measure_signal_rate as msr  # noqa: E402

TRAIN = ["2026-11-09", "2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13"]
TEST = ["2026-11-16", "2026-11-17", "2026-11-18", "2026-11-19", "2026-11-20"]
H3_SEED = 20261121
RIDE_ATR_MULT = 1.5
START_EQUITY = 100_000.0
ROUND_TRIP_COST = 1.99
OUT = HERE / "test_hypotheses_output.json"

HYPOTHESES = {
    "H0": {
        "label": "control: our invalidated rule",
        "entry": "close < SMA20 - 1.5*ATR20 AND close > prev close AND regime OK",
        "target": "SMA20 (reversion to the mean)",
        "kind": "sma20",
    },
    "H1": {
        "label": "logical complement: continuation",
        "entry": "close > SMA20 + 1.5*ATR20 AND close > prev close AND regime OK",
        "target": "entry + 1.5*ATR20 (ride, do not revert)",
        "kind": "ride",
    },
    "H2": {
        "label": "H1 with the regime condition inverted",
        "entry": "close > SMA20 + 1.5*ATR20 AND close > prev close AND SPY < SPY SMA60",
        "target": "entry + 1.5*ATR20 (ride, do not revert)",
        "kind": "ride",
    },
    "H3": {
        "label": "random control: is the exit structure the problem?",
        "entry": f"uniformly random eligible bars, seeded {H3_SEED}, "
                 "matched to H0's count per session",
        "target": "SMA20 (same exits as H0)",
        "kind": "sma20",
    },
}

COMMON = {
    "stop": "entry - 1.0*ATR20",
    "time_stop": "60 minutes",
    "session_stop": "15:50 ET",
    "entry_window": "10:00 <= bar start < 15:00 ET",
    "atr_floor": "config fees.min_atr20_usd per symbol (live gate)",
    "fees": "$0.99 buy + $1.00 sell = $1.99 per closed trade",
    "direction": "long only (shorting remains forbidden)",
}


# ---------------------------------------------------------------------------
# Fixed header
# ---------------------------------------------------------------------------

def print_header(min_atr: dict[str, float]) -> None:
    print("=" * 100)
    print("FIXED IN ADVANCE -- printed before any result in this file was computed")
    print("=" * 100)
    print("\nSPLIT (declared before computing anything)")
    print(f"  TRAIN  {' '.join(TRAIN)}   (5 sessions)")
    print(f"  TEST   {' '.join(TEST)}   (4.3 sessions; 11-20 feed ends 11:15 ET)")
    print("  Every hypothesis is reported on BOTH splits, separately, always.")
    print("  11-04..11-06 are used only as indicator warm-up (ATR20 seeds on the")
    print("  20th TR); they are in neither split.")
    print("\nHYPOTHESIS SET -- closed. Exactly these four. No others, no variants.")
    for hid, h in HYPOTHESES.items():
        print(f"\n  {hid}  {h['label']}")
        print(f"      entry   {h['entry']}")
        print(f"      target  {h['target']}")
    print("\nEXITS, IDENTICAL FOR ALL FOUR")
    for k, v in COMMON.items():
        print(f"  {k:14}{v}")
    print("\nATR FLOOR APPLIED (config/risk_limits.yaml, fees.min_atr20_usd)")
    for sym, gate in sorted(min_atr.items()):
        print(f"  {sym:8}{gate:.4f}")
    print("\nDATA AND CLEANING")
    print("  Bars: analysis/archive/bars_US_<SYM>_full.json (11-04..11-19) merged")
    print("  with analysis/live_<SYM>.json (11-17..11-20 11:15, live wins on")
    print("  duplicate timestamps). 5-minute RTH 09:35..15:55 ET, 16:00 dropped.")
    print("  Bad ticks: the committed rule evaluated per symbol on 11-09..11-19,")
    print("  same as measure_signal_rate.py; excluded at computation time only.")
    print("  Regime: SPY close > SPY SMA60 (60 five-minute bars, rolling, warm-up")
    print("  from 11-04). H2 instead requires SPY close <= SPY SMA60.")
    print("\nDEFINITIONS USED IN THE RESULT COLUMNS")
    print("  R       (exit - entry) / (1.0 * ATR20 at entry) -- gross price")
    print("          move in units of initial risk; commission is per order, not")
    print("          per unit, so it is reported in $ rather than folded into R.")
    print("  gross   sum of (exit - entry) per share, 1 share per signal.")
    print("  net     gross - $1.99 per closed trade, 1 share per signal.")
    print("  net@100k  engine.sizing.size_position at $100,000 equity applied to")
    print("          each closed trade; trades the engine refuses are counted and")
    print("          excluded from that column (the reason breakdown is printed).")
    print("  Fills are at the close of the bar that triggers, no slippage beyond")
    print("  the flat fee; a signal with no exit before the data ends is 'open'")
    print("  and excluded from closed-trade statistics.")
    print("=" * 100)


# ---------------------------------------------------------------------------
# Data and signals
# ---------------------------------------------------------------------------

def build_series():
    cfg = yaml.safe_load((ROOT / "config" / "risk_limits.yaml").read_text())
    min_atr = {k: float(v) for k, v in cfg["fees"]["min_atr20_usd"].items()}

    merged = {s: msr.load_merged(s) for s in ci.SYMBOLS}
    excluded: dict[str, set[int]] = {}
    flagged_all = {}
    for sym, bars in merged.items():
        window = [b for b in bars
                  if msr.BASELINE_FROM <= b["date"] <= msr.BASELINE_TO]
        _, flagged = ci.find_bad_ticks(window)
        flagged_all[sym] = flagged
        excluded[sym] = {f["tk"] for f in flagged}

    series = {sym: ci.compute_series(
        ci.session_bars([dict(b) for b in bars if b["tk"] not in excluded[sym]]))
        for sym, bars in merged.items()}
    spy_regime = msr.regime_series(series["SPY"])
    idx_by_sym = {sym: {bar["tk"]: i for i, bar in enumerate(bars)}
                  for sym, bars in series.items()}
    return cfg, min_atr, series, spy_regime, idx_by_sym, flagged_all


def regime_map(spy_regime):
    return {b["tk"]: b["regime_ok"] for b in spy_regime}


def eligible(bar: dict, min_atr: dict[str, float], sym: str) -> bool:
    if bar["sma20"] is None or bar["atr20"] is None or bar["prev_close"] is None:
        return False
    if not (ci.ENTRY_WINDOW_START_MIN <= bar["mins"] < ci.ENTRY_WINDOW_END_MIN):
        return False
    return bar["atr20"] >= min_atr[f"US.{sym}"]


def signals_for(hid: str, series, reg_by_tk, min_atr, dates: set[str]):
    out = []
    for sym, bars in series.items():
        for b in bars:
            if b["date"] not in dates:
                continue
            if not eligible(b, min_atr, sym):
                continue
            reg = reg_by_tk.get(b["tk"])
            if hid == "H0":
                if (b["c"] < b["sma20"] - ci.DISLOCATION_ATR_MULT * b["atr20"]
                        and b["c"] > b["prev_close"] and reg is True):
                    out.append((sym, b))
            elif hid == "H1":
                if (b["c"] > b["sma20"] + RIDE_ATR_MULT * b["atr20"]
                        and b["c"] > b["prev_close"] and reg is True):
                    out.append((sym, b))
            elif hid == "H2":
                if (b["c"] > b["sma20"] + RIDE_ATR_MULT * b["atr20"]
                        and b["c"] > b["prev_close"] and reg is False):
                    out.append((sym, b))
    return out


def h3_signals(series, min_atr, dates: set[str], h0_count_by_date: dict[str, int]):
    """Seeded uniform sample of eligible bars, k per session = H0's count."""
    rng = random.Random(H3_SEED)
    pool_by_date: dict[str, list[tuple[str, dict]]] = {}
    for sym in ci.SYMBOLS:
        for b in series[sym]:
            if b["date"] in dates and eligible(b, min_atr, sym):
                pool_by_date.setdefault(b["date"], []).append((sym, b))
    out = []
    for d in sorted(dates):
        k = h0_count_by_date.get(d, 0)
        pool = sorted(pool_by_date.get(d, []), key=lambda sx: (sx[1]["tk"], sx[0]))
        if k and pool:
            out.extend(rng.sample(pool, min(k, len(pool))))
    return out


# ---------------------------------------------------------------------------
# Exit simulation
# ---------------------------------------------------------------------------

def simulate_h(bars: list[dict], idx: int, kind: str) -> dict | None:
    e = bars[idx]
    stop = e["c"] - msr.STOP_ATR_MULT * e["atr20"]
    target_level = e["c"] + RIDE_ATR_MULT * e["atr20"] if kind == "ride" else None
    entry_dt = datetime.strptime(f"{e['date']} {e['hhmm']}", "%Y-%m-%d %H:%M")
    for j in range(idx + 1, len(bars)):
        b = bars[j]
        if b["date"] != e["date"]:
            return None
        held = (datetime.strptime(f"{b['date']} {b['hhmm']}", "%Y-%m-%d %H:%M")
                - entry_dt).total_seconds() / 60.0
        if b["mins"] >= msr.SESSION_FLAT_MIN:
            reason = "SESSION_STOP"
        elif b["c"] <= stop:
            reason = "STOP_HIT"
        elif kind == "ride" and b["c"] >= target_level:
            reason = "TARGET_HIT"
        elif kind == "sma20" and b["sma20"] is not None and b["c"] >= b["sma20"]:
            reason = "TARGET_HIT"
        elif held >= msr.TIME_STOP_MINUTES:
            reason = "TIME_STOP"
        else:
            continue
        return {
            "entry": e["c"], "entry_atr": e["atr20"], "stop": stop,
            "target_level": target_level,
            "exit": b["c"], "exit_et": b["hhmm"], "exit_date": b["date"],
            "held_minutes": round(held, 1), "reason": reason,
            "gross": b["c"] - e["c"],
            "net": b["c"] - e["c"] - ROUND_TRIP_COST,
        }
    return None


def run_signals(sig_list, series, idx_by_sym, kind):
    trades, opens = [], []
    for sym, x in sig_list:
        t = simulate_h(series[sym], idx_by_sym[sym][x["tk"]], kind)
        if t is None:
            opens.append((sym, x))
            continue
        t["symbol"] = sym
        t["entry_date"] = x["date"]
        t["entry_et"] = x["hhmm"]
        trades.append(t)
    return trades, opens


def engine_sizing(trades):
    from engine.config import RiskConfig
    from engine.errors import RiskViolation
    from engine.sizing import size_position

    rcfg = RiskConfig.load(ROOT / "config" / "risk_limits.yaml")
    net_usd, sized, unsized = 0.0, 0, {}
    detail = []
    for t in trades:
        try:
            s = size_position(rcfg, symbol=f"US.{t['symbol']}",
                              price=t["entry"], atr20=t["entry_atr"],
                              equity=START_EQUITY)
        except RiskViolation as exc:
            unsized[exc.args[0]] = unsized.get(exc.args[0], 0) + 1
            continue
        pnl = s.qty * (t["exit"] - t["entry"]) - ROUND_TRIP_COST
        net_usd += pnl
        sized += 1
        detail.append({"symbol": t["symbol"], "qty": s.qty,
                       "net_usd": round(pnl, 2)})
    return {"net_usd": net_usd, "n_sized": sized,
            "net_pct_of_equity": 100.0 * net_usd / START_EQUITY,
            "unsized": unsized, "detail": detail}


def stats(trades, n_signals, n_open):
    reasons: dict[str, int] = {}
    for t in trades:
        reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1
    wins = sum(1 for t in trades if t["net"] > 0)
    gross = sum(t["gross"] for t in trades)
    fees = ROUND_TRIP_COST * len(trades)
    net = sum(t["net"] for t in trades)
    rs = [t["gross"] / t["entry_atr"] for t in trades]
    eng = engine_sizing(trades)
    return {
        "n_signals": n_signals, "n_closed": len(trades), "n_open": n_open,
        "stop": reasons.get("STOP_HIT", 0),
        "target": reasons.get("TARGET_HIT", 0),
        "time": reasons.get("TIME_STOP", 0),
        "session": reasons.get("SESSION_STOP", 0),
        "wins": wins,
        "win_rate_pct": 100.0 * wins / len(trades) if trades else None,
        "gross": gross, "fees": fees, "net": net,
        "mean_net": net / len(trades) if trades else None,
        "mean_R": statistics.fmean(rs) if rs else None,
        "engine": eng,
        "trades": trades, "open_signals": n_open,
    }


# ---------------------------------------------------------------------------
# Sequential simulation (Part F's limits, TEST split only)
# ---------------------------------------------------------------------------

def sequential(sig_list, series, idx_by_sym, kind, cfg_path):
    from engine.config import RiskConfig
    from engine.errors import RiskViolation
    from engine.sizing import size_position

    rcfg = RiskConfig.load(cfg_path)
    raw = rcfg.raw
    max_conc = int(raw["sizing"]["max_concurrent_positions"])
    cluster_members = {m.split(".")[-1] for m in
                       raw["universe"]["clusters"]["index_broad"]["members"]}
    cluster_cap = int(raw["universe"]["clusters"]["index_broad"]
                      ["max_concurrent_positions"])
    max_entries = int(raw["frequency"]["max_entries_per_session"])
    min_gap_s = int(raw["frequency"]["min_seconds_between_entries"])
    buy_fee = float(raw["fees"]["commission_buy_reservation_usd"])
    sell_fee = float(raw["fees"]["commission_per_filled_order_usd"])
    stop_mult = float(raw["sizing"]["stop_distance_atr_mult"])

    signals = {(sym, x["tk"]) for sym, x in sig_list}
    events: dict[int, list[tuple[str, int]]] = {}
    for sym, bars in series.items():
        for i, bar in enumerate(bars):
            events.setdefault(bar["tk"], []).append((sym, i))

    open_pos: dict[str, dict] = {}
    realised = 0.0
    session_date = None
    entries_today = 0
    last_entry_tk = None
    taken, skipped = [], {}
    last_close: dict[str, float] = {}
    curve = [START_EQUITY]
    peak, max_dd = START_EQUITY, 0.0

    def skip(sym, tk, reason):
        skipped[reason] = skipped.get(reason, 0) + 1
        skipped.setdefault("_signals", []).append(
            {"symbol": sym, "time_key": tk, "reason": reason})

    def exit_reason(bar, pos):
        held = (datetime.strptime(f"{bar['date']} {bar['hhmm']}",
                                  "%Y-%m-%d %H:%M")
                - pos["entry_dt"]).total_seconds() / 60.0
        if bar["mins"] >= msr.SESSION_FLAT_MIN:
            return "SESSION_STOP", held
        if bar["c"] <= pos["stop"]:
            return "STOP_HIT", held
        if kind == "ride" and bar["c"] >= pos["target"]:
            return "TARGET_HIT", held
        if kind == "sma20" and bar["sma20"] is not None and bar["c"] >= bar["sma20"]:
            return "TARGET_HIT", held
        if held >= msr.TIME_STOP_MINUTES:
            return "TIME_STOP", held
        return None, held

    for tk in sorted(events):
        for sym, idx in sorted(events[tk]):
            bar = series[sym][idx]
            last_close[sym] = bar["c"]
            if bar["date"] != session_date:
                session_date = bar["date"]
                entries_today = 0

            pos = open_pos.get(sym)
            if pos is not None and idx > pos["idx"]:
                if bar["date"] != pos["date"]:
                    reason, held = "DATE_ROLLOVER", 0.0
                else:
                    reason, held = exit_reason(bar, pos)
                if reason:
                    gross = pos["qty"] * (bar["c"] - pos["entry"])
                    net = gross - buy_fee - sell_fee
                    realised += net
                    taken.append({
                        "symbol": sym, "entry_date": pos["date"],
                        "entry_et": pos["entry_et"], "entry": pos["entry"],
                        "qty": pos["qty"], "notional_usd": pos["notional"],
                        "stop": pos["stop"], "exit_date": bar["date"],
                        "exit_et": bar["hhmm"], "exit": bar["c"],
                        "held_minutes": round(held, 1), "reason": reason,
                        "gross_usd": gross, "fees_usd": buy_fee + sell_fee,
                        "net_usd": net,
                    })
                    del open_pos[sym]
                    pos = None

            if (sym, tk) in signals:
                if sym in open_pos:
                    skip(sym, tk, "ALREADY_HOLDING")
                elif len(open_pos) >= max_conc:
                    skip(sym, tk, "POSITION_COUNT_LIMIT")
                elif sym in cluster_members and any(
                        s in cluster_members for s in open_pos):
                    skip(sym, tk, "CORRELATION_CLUSTER_LIMIT")
                elif entries_today >= max_entries:
                    skip(sym, tk, "ENTRY_BUDGET_EXHAUSTED")
                elif (last_entry_tk is not None
                      and (tk - last_entry_tk) < min_gap_s * 1000):
                    skip(sym, tk, "ENTRY_TOO_SOON")
                else:
                    try:
                        size = size_position(rcfg, symbol=f"US.{sym}",
                                             price=bar["c"], atr20=bar["atr20"],
                                             equity=START_EQUITY)
                    except RiskViolation as exc:
                        skip(sym, tk, f"SIZING_{exc.args[0]}")
                    else:
                        cash = START_EQUITY + realised - sum(
                            p["notional"] + buy_fee for p in open_pos.values())
                        if size.notional_usd + buy_fee > cash:
                            skip(sym, tk, "INSUFFICIENT_CASH")
                        else:
                            entry_dt = datetime.strptime(
                                f"{bar['date']} {bar['hhmm']}", "%Y-%m-%d %H:%M")
                            open_pos[sym] = {
                                "date": bar["date"], "entry_et": bar["hhmm"],
                                "idx": idx, "entry": bar["c"], "qty": size.qty,
                                "notional": size.notional_usd,
                                "stop": bar["c"] - stop_mult * bar["atr20"],
                                "target": bar["c"] + RIDE_ATR_MULT * bar["atr20"],
                                "entry_dt": entry_dt,
                            }
                            entries_today += 1
                            last_entry_tk = tk

        equity = START_EQUITY + realised
        for s, p in open_pos.items():
            equity += p["qty"] * (last_close[s] - p["entry"]) - buy_fee
        curve.append(equity)
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    still_open = [{"symbol": s, "entry_date": p["date"],
                   "entry_et": p["entry_et"], "entry": p["entry"],
                   "qty": p["qty"], "stop": p["stop"]}
                  for s, p in open_pos.items()]
    net = sum(t["net_usd"] for t in taken)
    reasons: dict[str, int] = {}
    for t in taken:
        reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1
    wins = sum(1 for t in taken if t["net_usd"] > 0)
    return {
        "limits": {"max_concurrent": max_conc, "cluster_cap": cluster_cap,
                   "max_entries_per_session": max_entries,
                   "min_seconds_between_entries": min_gap_s,
                   "buy_fee": buy_fee, "sell_fee": sell_fee},
        "n_signals": len(signals), "n_taken": len(taken),
        "n_open": len(still_open), "exits": reasons,
        "not_taken": {k: v for k, v in skipped.items() if k != "_signals"},
        "not_taken_detail": skipped.get("_signals", []),
        "realised_net_usd": net,
        "realised_pct_of_equity": 100.0 * net / START_EQUITY,
        "max_drawdown_usd": max_dd,
        "max_drawdown_pct_of_equity": 100.0 * max_dd / START_EQUITY,
        "win_rate_pct": 100.0 * wins / len(taken) if taken else None,
        "trades": taken, "still_open": still_open,
    }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def print_split(name: str, dates: list[str], results: dict) -> None:
    print(f"\n\n{name} SPLIT  {' '.join(dates)}")
    print("-" * 100)
    hdr = (f"{'hyp':4}{'n':>4}{'clsd':>6}{'open':>6}{'stop':>6}{'tgt':>5}"
           f"{'time':>6}{'sess':>6}{'win%':>7}{'gross':>9}{'fees':>8}{'net':>9}"
           f"{'meanR':>8}")
    print(hdr)
    for hid in HYPOTHESES:
        s = results[hid]
        wr = f"{s['win_rate_pct']:.1f}" if s["win_rate_pct"] is not None else "n/a"
        mr = f"{s['mean_R']:+.3f}" if s["mean_R"] is not None else "n/a"
        print(f"{hid:4}{s['n_signals']:>4}{s['n_closed']:>6}{s['n_open']:>6}"
              f"{s['stop']:>6}{s['target']:>5}{s['time']:>6}{s['session']:>6}"
              f"{wr:>7}{s['gross']:>+9.2f}{s['fees']:>8.2f}{s['net']:>+9.2f}"
              f"{mr:>8}")
    print(f"\n{'hyp':4}{'sized':>7}{'unsized':>9}{'net@100k$':>13}{'net%@100k':>12}"
          f"   refused by engine")
    for hid in HYPOTHESES:
        e = results[hid]["engine"]
        refused = ", ".join(f"{k} {v}" for k, v in sorted(e["unsized"].items()))
        print(f"{hid:4}{e['n_sized']:>7}{sum(e['unsized'].values()):>9}"
              f"{e['net_usd']:>+13.2f}{e['net_pct_of_equity']:>+11.4f}%"
              f"   {refused or '-'}")
    print("\n  n = signals found; clsd = closed trades; open = signal with no exit")
    print("  before the data edge (excluded from win/gross/fees/net/meanR).")
    print("  net@100k is the sum over trades the engine would actually size; the")
    print("  unsized count is printed so the denominator cannot be mistaken.")


def print_sequential(name: str, hid: str, r: dict) -> None:
    print(f"\n\n{name} -- SEQUENTIAL SIMULATION, {hid} ON TEST ONLY")
    print("-" * 100)
    lim = r["limits"]
    print(f"Limits from config: max_concurrent {lim['max_concurrent']}, "
          f"index-broad cluster max {lim['cluster_cap']}, "
          f"max_entries/session {lim['max_entries_per_session']}, "
          f"min gap {lim['min_seconds_between_entries']}s.")
    print(f"Fees ${lim['buy_fee']:.2f} buy + ${lim['sell_fee']:.2f} sell per ORDER. "
          f"Sizing via engine.sizing.size_position at ${START_EQUITY:,.0f}.")
    print("-" * 100)
    if r["trades"]:
        print(f"{'symbol':8}{'entry':18}{'qty':>5}{'notional':>13}"
              f"{'  exit':18}{'reason':>13}{'gross$':>9}{'fees$':>7}{'net$':>8}")
        for t in r["trades"]:
            print(f"{t['symbol']:8}{t['entry_date'] + ' ' + t['entry_et']:18}"
                  f"{t['qty']:>5}{t['notional_usd']:>13,.2f}"
                  f"{'  ' + t['exit_date'] + ' ' + t['exit_et']:18}"
                  f"{t['reason']:>13}{t['gross_usd']:>+9.2f}"
                  f"{t['fees_usd']:>7.2f}{t['net_usd']:>+8.2f}")
    for p in r["still_open"]:
        print(f"{p['symbol']:8}{p['entry_date'] + ' ' + p['entry_et']:18}"
              f"{p['qty']:>5}{'':>11}{'still open':18}{'OPEN':>13}")
    print(f"\ntaken {r['n_taken']} of {r['n_signals']} signals "
          f"({r['n_open']} still open at the data edge)")
    print("  exits: " + (", ".join(f"{k} {v}" for k, v in sorted(r["exits"].items()))
                        or "none"))
    if r["not_taken"]:
        print("  not taken:")
        for reason in sorted(r["not_taken"]):
            print(f"    {reason:32}{r['not_taken'][reason]:>3}")
    print(f"\nrealised P&L  ${r['realised_net_usd']:+,.2f}  = "
          f"{r['realised_pct_of_equity']:+.4f}% of ${START_EQUITY:,.0f} equity")
    print(f"max drawdown  ${r['max_drawdown_usd']:,.2f}  = "
          f"{r['max_drawdown_pct_of_equity']:.4f}% (mark-to-market every 5-min")
    print("              close; includes open positions at last close minus")
    print("              reserved buy commission)")
    if r["win_rate_pct"] is not None:
        print(f"win rate      {r['win_rate_pct']:.1f}%")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    cfg, min_atr, series, spy_regime, idx_by_sym, flagged_all = build_series()
    print_header(min_atr)
    print(f"\nBad ticks excluded by the committed rule: "
          f"{sum(len(v) for v in flagged_all.values())} bars")
    for sym, fl in flagged_all.items():
        for f in fl:
            print(f"  {sym} {f['date']} {f['hhmm']} ET")

    reg_by_tk = regime_map(spy_regime)
    splits = {"TRAIN": set(TRAIN), "TEST": set(TEST)}
    sigs: dict[str, dict[str, list]] = {}
    results: dict[str, dict] = {}

    for name, dates in splits.items():
        sigs[name] = {}
        house = {}
        for hid in ("H0", "H1", "H2"):
            sigs[name][hid] = signals_for(hid, series, reg_by_tk, min_atr, dates)
        h0_per_date: dict[str, int] = {}
        for _, x in sigs[name]["H0"]:
            h0_per_date[x["date"]] = h0_per_date.get(x["date"], 0) + 1
        sigs[name]["H3"] = h3_signals(series, min_atr, dates, h0_per_date)
        for hid in HYPOTHESES:
            trades, opens = run_signals(sigs[name][hid], series, idx_by_sym,
                                        HYPOTHESES[hid]["kind"])
            results[name] = results.get(name, {})
            results[name][hid] = stats(trades, len(sigs[name][hid]), len(opens))

    for name, dates in (("TRAIN", TRAIN), ("TEST", TEST)):
        print_split(name, dates, results[name])

    print("\n\n" + "=" * 100)
    print("SEQUENTIAL SIMULATION -- TEST SPLIT ONLY, H1 AND H2")
    print("=" * 100)
    seq = {}
    for hid in ("H1", "H2"):
        seq[hid] = sequential(sigs["TEST"][hid], series, idx_by_sym,
                              HYPOTHESES[hid]["kind"],
                              ROOT / "config" / "risk_limits.yaml")
        print_sequential("TEST", hid, seq[hid])

    print("\n" + "-" * 100)
    print("WHAT THIS IS AND IS NOT")
    print("All four hypotheses were fixed before the numbers above existed, and")
    print("the set is closed: nothing was added after seeing a result. The sample")
    print("is 9 sessions of 5-minute bars (TEST includes a 0.3-session tail) and")
    print("n per hypothesis is small; exits are close-based with no slippage")
    print("beyond the flat fee. A positive number here is not evidence of edge,")
    print("and a negative one is not proof of none. No config or engine value is")
    print("changed, and nothing here selects a parameter. If none of the four")
    print("works, that is the answer.")
    print("-" * 100)

    payload = {
        "generated_by": "analysis/test_hypotheses.py",
        "fixed_in_advance": {
            "hypotheses": HYPOTHESES, "common": COMMON,
            "train": TRAIN, "test": TEST, "h3_seed": H3_SEED,
            "atr_floor": min_atr,
            "bad_ticks_excluded": {
                s: [{k: f[k] for k in ("date", "hhmm", "tk")} for f in fl]
                for s, fl in flagged_all.items()},
        },
        "note": "measurement, not parameter selection; no strategy or config "
                "change has been made",
        "results": {
            name: {hid: {k: v for k, v in s.items() if k != "open_signals"}
                   for hid, s in res.items()}
            for name, res in results.items()
        },
        "sequential_test": seq,
    }
    OUT.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"\nWrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
