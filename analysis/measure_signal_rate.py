#!/usr/bin/env python3
"""What the regime filter and the ATR floor cost us, measured rather than assumed.

Reads analysis/archive/bars_US_<SYM>_full.json (2026-11-04..11-19) merged with
analysis/live_<SYM>.json (2026-11-17..2026-11-20, live wins on duplicate
timestamps), and answers four questions:

  PART A  How much of each session was the regime filter open?
  PART B  How many entries survive each gate, against the 157 baseline
          measured WITHOUT the regime and ATR gates?
  PART C  When a session closes with SPY below its SMA60, how far into the
          next session does the regime reopen?
  PART D  EXPLORATORY ONLY -- what did the surviving signals do under the
          pre-committed exits? No position sizing, no portfolio constraints.

With --extend three more measurement modes run after Parts A-D (the A-D
output is unchanged whether or not the flag is present):

  PART E  The same exit sim on every base signal, so the 22 regime-OK signals
          can be compared with the 148 the joint filter removed.
  PART F  The same signals run SEQUENTIALLY through the real portfolio limits
          (3 concurrent, 1 index-broad, 6 entries/session, 300s apart) with
          quantity from engine.sizing.size_position at $100,000 equity.
  PART G  How far below the stop the stop exits actually closed, in ATR units.

Parts E-G are measurement, not parameter selection. No strategy change has
been made; nothing here feeds a config value.

DEFINITIONS are imported from analysis/compute_indicators.py rather than
restated, so a drift in one cannot silently disagree with the other: 5-minute
RTH bars 09:35..15:55 ET (16:00 print dropped), bad-tick quarantine at
10x median range / 1% wick, Wilder ATR20 seeded on the first 20 TRs, SMA20
inclusive of the current close, entry window 10:00 <= t < 15:00 ET.

Run:  python3 analysis/measure_signal_rate.py            # Parts A-D
      python3 analysis/measure_signal_rate.py --extend   # Parts A-G
Also writes: analysis/signal_rate_output.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from datetime import datetime, timedelta
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))   # Part F imports engine.sizing / engine.config

import compute_indicators as ci  # noqa: E402

WINDOW_FROM = "2026-11-09"
WINDOW_TO = "2026-11-20"          # live data ends 11-20 11:15, a partial session
BASELINE_FROM = "2026-11-09"      # the window the 157 figure was measured over
BASELINE_TO = "2026-11-19"
REGIME_SMA_PERIOD = 60
STOP_ATR_MULT = 1.0
TIME_STOP_MINUTES = 60
SESSION_FLAT_MIN = 15 * 60 + 50   # 15:50 ET
ROUND_TRIP_COST = 1.99            # config: 0.99 buy reservation + 1.00 measured sell
START_EQUITY = 100_000.0          # Part F sizing equity, as instructed
OUT = HERE / "signal_rate_output.json"


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------

def load_merged(symbol: str) -> list[dict]:
    """Archive bars, then live bars on top; live wins where timestamps collide."""
    rows: dict[int, dict] = {}
    for path in (HERE / "archive" / f"bars_US_{symbol}_full.json",
                 HERE / f"live_{symbol}.json"):
        raw = json.loads(path.read_text())
        for b in raw:
            ts = datetime.fromtimestamp(b["time_key"] / 1000, ci.ET)
            rows[b["time_key"]] = {
                "tk": b["time_key"],
                "date": ts.strftime("%Y-%m-%d"),
                "hhmm": ts.strftime("%H:%M"),
                "mins": ts.hour * 60 + ts.minute,
                "o": b["open"], "h": b["high"], "l": b["low"],
                "c": b["close"], "v": b["volume"],
            }
    return [rows[k] for k in sorted(rows)]


def regime_series(spy_clean: list[dict]) -> list[dict]:
    """SPY close vs its own rolling SMA60, on the bad-tick-cleaned series."""
    closes = [b["c"] for b in spy_clean]
    out = []
    for i, b in enumerate(spy_clean):
        if i + 1 >= REGIME_SMA_PERIOD:
            sma60 = sum(closes[i + 1 - REGIME_SMA_PERIOD:i + 1]) / REGIME_SMA_PERIOD
            d = dict(b)
            d["sma60"] = sma60
            d["regime_ok"] = b["c"] > sma60
        else:
            d = dict(b)
            d["sma60"] = None
            d["regime_ok"] = None
        out.append(d)
    return out


# ---------------------------------------------------------------------------
# PART A
# ---------------------------------------------------------------------------

def part_a(spy_regime: list[dict]) -> dict:
    rows = [b for b in spy_regime if WINDOW_FROM <= b["date"] <= WINDOW_TO
            and b["regime_ok"] is not None]
    per_session = {}
    for d in sorted({b["date"] for b in rows}):
        sb = [b for b in rows if b["date"] == d]
        ok = [b for b in sb if b["regime_ok"]]
        flips = [(sb[i]["hhmm"], "OK" if sb[i]["regime_ok"] else "BLOCKED")
                 for i in range(1, len(sb))
                 if sb[i]["regime_ok"] != sb[i - 1]["regime_ok"]]
        per_session[d] = {
            "bars": len(sb),
            "ok": len(ok),
            "blocked": len(sb) - len(ok),
            "pct_tradable": 100.0 * len(ok) / len(sb),
            "opens": "OK" if sb[0]["regime_ok"] else "BLOCKED",
            "closes": "OK" if sb[-1]["regime_ok"] else "BLOCKED",
            "transitions": len(flips),
            "first_flip_et": flips[0][0] if flips else None,
            "last_flip_et": flips[-1][0] if flips else None,
        }
    total_ok = sum(r["ok"] for r in per_session.values())
    total_bars = sum(r["bars"] for r in per_session.values())
    full = {d: r for d, r in per_session.items() if d <= BASELINE_TO}
    full_ok = sum(r["ok"] for r in full.values())
    full_bars = sum(r["bars"] for r in full.values())
    return {
        "per_session": per_session,
        "all_bars": total_bars,
        "ok_bars": total_ok,
        "pct_ok_all": 100.0 * total_ok / total_bars if total_bars else 0.0,
        "full_session_bars": full_bars,
        "full_session_ok": full_ok,
        "pct_ok_full": 100.0 * full_ok / full_bars if full_bars else 0.0,
    }


# ---------------------------------------------------------------------------
# PART B
# ---------------------------------------------------------------------------

def part_b(series: dict[str, list[dict]], spy_regime: list[dict],
           min_atr: dict[str, float]) -> dict:
    regime_by_tk = {b["tk"]: b for b in spy_regime}
    out = {}
    for sym, bars in series.items():
        base, atr_only, reg_only, joint = [], [], [], []
        for b in bars:
            if not (WINDOW_FROM <= b["date"] <= WINDOW_TO):
                continue
            if b["sma20"] is None or b["atr20"] is None or b["prev_close"] is None:
                continue
            if not (ci.ENTRY_WINDOW_START_MIN <= b["mins"] < ci.ENTRY_WINDOW_END_MIN):
                continue
            dislocated = b["c"] < b["sma20"] - ci.DISLOCATION_ATR_MULT * b["atr20"]
            if not (dislocated and b["c"] > b["prev_close"]):
                continue
            reg = regime_by_tk.get(b["tk"], {}).get("regime_ok")
            atr_ok = b["atr20"] >= min_atr[f"US.{sym}"]
            base.append(b)
            if atr_ok:
                atr_only.append(b)
            if reg:
                reg_only.append(b)
            if reg and atr_ok:
                joint.append(b)
        out[sym] = {
            "base": base, "atr_only": atr_only,
            "regime_only": reg_only, "joint": joint,
            "per_session": {
                d: sum(1 for b in joint if b["date"] == d)
                for d in sorted({b["date"] for b in base})
            },
            "base_per_session": {
                d: sum(1 for b in base if b["date"] == d)
                for d in sorted({b["date"] for b in base})
            },
        }
    return out


# ---------------------------------------------------------------------------
# PART C
# ---------------------------------------------------------------------------

def part_c(spy_regime: list[dict]) -> list[dict]:
    rows = [b for b in spy_regime if WINDOW_FROM <= b["date"] <= WINDOW_TO
            and b["regime_ok"] is not None]
    sessions = sorted({b["date"] for b in rows})
    out = []
    for i, d in enumerate(sessions[:-1]):
        last = [b for b in rows if b["date"] == d][-1]
        if last["regime_ok"]:
            continue
        nxt = sessions[i + 1]
        sb = [b for b in rows if b["date"] == nxt]
        hit = next((k for k, b in enumerate(sb) if b["regime_ok"]), None)
        out.append({
            "closed_blocked": d,
            "next_session": nxt,
            "bars_into_session": None if hit is None else hit + 1,
            "minutes_into_session": None if hit is None else sb[hit]["mins"] - ci.SESSION_START_MIN,
            "recovered_at_et": None if hit is None else sb[hit]["hhmm"],
            "next_session_bars": len(sb),
            "next_session_ok_bars": sum(1 for b in sb if b["regime_ok"]),
        })
    return out


# ---------------------------------------------------------------------------
# PART D -- exploratory only
# ---------------------------------------------------------------------------

def simulate(bars: list[dict], idx: int) -> dict:
    e = bars[idx]
    stop = e["c"] - STOP_ATR_MULT * e["atr20"]
    entry_dt = datetime.strptime(f"{e['date']} {e['hhmm']}", "%Y-%m-%d %H:%M")
    for j in range(idx + 1, len(bars)):
        b = bars[j]
        if b["date"] != e["date"]:
            return None  # session ended before any exit rule fired
        held = (datetime.strptime(f"{b['date']} {b['hhmm']}", "%Y-%m-%d %H:%M")
                - entry_dt).total_seconds() / 60.0
        if b["mins"] >= SESSION_FLAT_MIN:
            reason = "SESSION_STOP"
        elif b["c"] <= stop:
            reason = "STOP_HIT"
        elif b["sma20"] is not None and b["c"] >= b["sma20"]:
            reason = "TARGET_HIT"
        elif held >= TIME_STOP_MINUTES:
            reason = "TIME_STOP"
        else:
            continue
        return {
            "entry": e["c"], "entry_atr": e["atr20"], "stop": stop,
            "exit": b["c"], "exit_et": b["hhmm"], "exit_date": b["date"],
            "exit_sma20": b["sma20"], "held_minutes": round(held, 1),
            "reason": reason, "gross": b["c"] - e["c"],
            "net": b["c"] - e["c"] - ROUND_TRIP_COST,
        }
    return None  # session data ran out before any exit rule fired


# ---------------------------------------------------------------------------
# PARTS E-G -- measurement modes (--extend). Not parameter selection.
# ---------------------------------------------------------------------------

def _run_signals(sig_list, series, idx_by_sym):
    """simulate() over a list of (symbol, bar); same rules as Part D."""
    trades, opens = [], []
    for sym, x in sig_list:
        t = simulate(series[sym], idx_by_sym[sym][x["tk"]])
        if t is None:
            opens.append((sym, x))
            continue
        t["symbol"] = sym
        t["entry_date"] = x["date"]
        t["entry_et"] = x["hhmm"]
        trades.append(t)
    return trades, opens


def _group_stats(trades, n_signals, n_open):
    reasons: dict[str, int] = {}
    for t in trades:
        reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1
    wins = [t for t in trades if t["net"] > 0]
    gross = sum(t["gross"] for t in trades)
    fees = ROUND_TRIP_COST * len(trades)
    net = sum(t["net"] for t in trades)
    return {
        "n_signals": n_signals,
        "n_closed": len(trades),
        "n_open": n_open,
        "stop": reasons.get("STOP_HIT", 0),
        "target": reasons.get("TARGET_HIT", 0),
        "time": reasons.get("TIME_STOP", 0),
        "session": reasons.get("SESSION_STOP", 0),
        "wins": len(wins),
        "win_rate_pct": 100.0 * len(wins) / len(trades) if trades else None,
        "gross": gross,
        "fees": fees,
        "net": net,
        "mean_net": net / len(trades) if trades else None,
        "reasons": reasons,
    }


def part_e(b, series, idx_by_sym, spy_regime, min_atr, d_trades, d_opens):
    """Ablation: joint filter ON vs regime OFF, same exits, 1 share each."""
    regime_by_tk = {x["tk"]: x for x in spy_regime}
    all_sigs = [(sym, x) for sym, r in b.items() for x in r["base"]]
    joint_ids = {(sym, x["tk"]) for sym, r in b.items() for x in r["joint"]}
    ok_sigs = [(s, x) for s, x in all_sigs if (s, x["tk"]) in joint_ids]
    blocked_sigs = [(s, x) for s, x in all_sigs if (s, x["tk"]) not in joint_ids]
    atr_sigs = [(sym, x) for sym, r in b.items() for x in r["atr_only"]]

    regime_false = [x for _, x in all_sigs
                    if not regime_by_tk.get(x["tk"], {}).get("regime_ok")]
    atr_fail = [(s, x) for s, x in all_sigs
                if x["atr20"] < min_atr[f"US.{s}"]]
    regime_ok_but_atr_fail = [(s, x) for s, x in atr_fail
                              if regime_by_tk.get(x["tk"], {}).get("regime_ok")]

    ok_stats = _group_stats(d_trades, len(ok_sigs), len(d_opens))
    blocked_trades, blocked_opens = _run_signals(blocked_sigs, series, idx_by_sym)
    all_trades, all_opens = _run_signals(all_sigs, series, idx_by_sym)
    atr_trades, atr_opens = _run_signals(atr_sigs, series, idx_by_sym)
    blocked_stats = _group_stats(blocked_trades, len(blocked_sigs),
                                 len(blocked_opens))
    all_stats = _group_stats(all_trades, len(all_sigs), len(all_opens))
    atr_stats = _group_stats(atr_trades, len(atr_sigs), len(atr_opens))

    print("\n\nPART E -- REGIME FILTER ABLATION " + "-" * 62)
    print("Same exits as Part D (stop -1.0 ATR, target SMA20, 60-min time stop,")
    print("flat by 15:50, close fills), 1 share, $1.99 per trade. Groups are")
    print("signal sets, not portfolios: no concurrency, cluster or entry caps.")
    print("-" * 96 + "\n")
    rows = [
        (f"regime OK (joint, n={ok_stats['n_signals']})", ok_stats),
        (f"removed by joint filter (n={len(blocked_sigs)})", blocked_stats),
        (f"all base signals (n={len(all_sigs)})", all_stats),
        (f"ATR gate only, regime off (n={len(atr_sigs)})", atr_stats),
    ]
    print(f"{'group':38}{'n':>5}{'closed':>8}{'open':>6}{'stop':>6}{'tgt':>5}"
          f"{'time':>6}{'sess':>6}{'win%':>7}{'gross':>9}{'fees':>8}{'net':>9}"
          f"{'mean':>8}")
    for label, s in rows:
        wr = f"{s['win_rate_pct']:.1f}" if s["win_rate_pct"] is not None else "n/a"
        mn = f"{s['mean_net']:+.2f}" if s["mean_net"] is not None else "n/a"
        print(f"{label:38}{s['n_signals']:>5}{s['n_closed']:>8}{s['n_open']:>6}"
              f"{s['stop']:>6}{s['target']:>5}{s['time']:>6}{s['session']:>6}"
              f"{wr:>7}{s['gross']:>+9.2f}{s['fees']:>8.2f}{s['net']:>+9.2f}"
              f"{mn:>8}")
    print(f"\nComposition of the {len(blocked_sigs)} group: {len(regime_false)} are "
          f"regime-false (SPY <= SMA60); "
          f"{len(regime_ok_but_atr_fail)} is regime-true but fails the ATR floor")
    for s, x in regime_ok_but_atr_fail:
        print(f"  {s} {x['date']} {x['hhmm']} ET  ATR20 {x['atr20']:.4f} < "
              f"gate {min_atr[f'US.{s}']:.4f}")
    print(f"ATR floor removes {len(atr_fail)} of {len(all_sigs)} base signals in total.")
    print("Fees here are $1.99 x closed trades at 1 share, so they dominate a")
    print("signal set this small; compare groups on gross and on win rate, not")
    print("on net alone.")
    print("\nReading: the blocked group is the counterfactual for the regime filter.")
    print("If it outperforms the regime-OK group on this sample, the filter cost")
    print("money here; that is a finding about this window, not a licence to change")
    print("the rule (see the closing note).")
    return {
        "note": "measurement, not parameter selection",
        "groups": {label: s for label, s in rows},
        "blocked_composition": {
            "regime_false": len(regime_false),
            "regime_ok_but_atr_fail": len(regime_ok_but_atr_fail),
            "atr_fail_total": len(atr_fail),
        },
        "blocked_trades": blocked_trades,
    }


def _exit_reason(bar, entry_dt, stop):
    held = (datetime.strptime(f"{bar['date']} {bar['hhmm']}", "%Y-%m-%d %H:%M")
            - entry_dt).total_seconds() / 60.0
    if bar["mins"] >= SESSION_FLAT_MIN:
        return "SESSION_STOP", held
    if bar["c"] <= stop:
        return "STOP_HIT", held
    if bar["sma20"] is not None and bar["c"] >= bar["sma20"]:
        return "TARGET_HIT", held
    if held >= TIME_STOP_MINUTES:
        return "TIME_STOP", held
    return None, held


def part_f(b, series, cfg_path, d_trades):
    """Sequential simulation of the 22 joint signals through the real limits."""
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

    signals = {(sym, x["tk"]) for sym, r in b.items() for x in r["joint"]}
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
                    reason, held = _exit_reason(bar, pos["entry_dt"], pos["stop"])
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
                                "entry_dt": entry_dt,
                                "binding": size.binding_constraint,
                            }
                            entries_today += 1
                            last_entry_tk = tk

        equity = START_EQUITY + realised
        for s, p in open_pos.items():
            equity += p["qty"] * (last_close[s] - p["entry"]) - buy_fee
        curve.append(equity)
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    still_open = [
        {"symbol": s, "entry_date": p["date"], "entry_et": p["entry_et"],
         "entry": p["entry"], "qty": p["qty"], "stop": p["stop"]}
        for s, p in open_pos.items()
    ]
    n_closed = len(taken)
    net = sum(t["net_usd"] for t in taken)
    reasons: dict[str, int] = {}
    for t in taken:
        reasons[t["reason"]] = reasons.get(t["reason"], 0) + 1

    print("\n\nPART F -- SEQUENTIAL SIMULATION WITH REAL PORTFOLIO LIMITS " + "-" * 29)
    print("Signals: the 22 joint (ATR + regime) signals, processed in timestamp")
    print("order. Within one timestamp, symbols are processed alphabetically; the")
    print(f"{min_gap_s}s entry gap means only the first can enter. Sizing from")
    print("engine.sizing.size_position with equity $100,000 (not reimplemented).")
    print(f"Limits from config: max_concurrent {max_conc}, index-broad cluster "
          f"max {cluster_cap},")
    print(f"max_entries/session {max_entries}, min gap {min_gap_s}s. "
          f"Fees ${buy_fee:.2f} buy + ${sell_fee:.2f} sell per ORDER.")
    print("-" * 96 + "\n")
    if taken:
        print(f"{'symbol':8}{'entry':18}{'qty':>5}{'notional':>13}"
              f"{'  exit':18}{'reason':>13}{'gross$':>9}{'fees$':>7}{'net$':>8}")
        for t in taken:
            print(f"{t['symbol']:8}{t['entry_date'] + ' ' + t['entry_et']:18}"
                  f"{t['qty']:>5}{t['notional_usd']:>13,.2f}"
                  f"{'  ' + t['exit_date'] + ' ' + t['exit_et']:18}"
                  f"{t['reason']:>13}"
                  f"{t['gross_usd']:>+9.2f}{t['fees_usd']:>7.2f}"
                  f"{t['net_usd']:>+8.2f}")
    for p in still_open:
        print(f"{p['symbol']:8}{p['entry_date'] + ' ' + p['entry_et']:18}"
              f"{p['qty']:>5}{'':>11}{'still open':18}{'OPEN':>13}")
    print(f"\ntaken {n_closed} of {len(signals)} signals "
          f"({len(still_open)} still open at the data edge)")
    print("  exits: " + ", ".join(f"{k} {v}" for k, v in sorted(reasons.items())))
    if skipped:
        print("  not taken:")
        for reason in sorted(k for k in skipped if k != "_signals"):
            print(f"    {reason:32}{skipped[reason]:>3}")
    print(f"\nrealised P&L  ${net:+,.2f}  = {100.0 * net / START_EQUITY:+.4f}% "
          f"of ${START_EQUITY:,.0f} equity")
    print(f"max drawdown  ${max_dd:,.2f}  = {100.0 * max_dd / START_EQUITY:.4f}% "
          f"(mark-to-market at every 5-min close; includes open positions at")
    print("              last close minus reserved buy commission)")
    if taken:
        w = sum(1 for t in taken if t["net_usd"] > 0)
        print(f"win rate      {100.0 * w / n_closed:.1f}% ({w}/{n_closed})")
        print(f"Part D's independent 1-share net for all 22 was "
              f"${sum(t['net'] for t in d_trades):+,.2f}; the {n_closed} trades "
              f"above are a subset at real position size, so compare selection")
        print("and Win/Loss pattern, not the dollar totals, across the two.")
        first = taken[0]
        print(f"\nfirst taken trade arithmetic ({first['symbol']} "
              f"{first['entry_date']} {first['entry_et']} ET):")
        print(f"  price {first['entry']:.4f}, qty {first['qty']} = "
              f"${first['notional_usd']:,.2f} notional "
              f"(15% cap = ${START_EQUITY * 0.15:,.2f})")
        print(f"  stop  = {first['entry']:.4f} - {stop_mult:.2f}*ATR = "
              f"{first['stop']:.4f}")
        print(f"  exit  {first['exit_date']} {first['exit_et']} ET at "
              f"{first['exit']:.4f} ({first['reason']})")
        print(f"  gross = {first['qty']} * ({first['exit']:.4f} - "
              f"{first['entry']:.4f}) = ${first['gross_usd']:+,.2f}")
        print(f"  net   = ${first['gross_usd']:+,.2f} - "
              f"${first['fees_usd']:.2f} = ${first['net_usd']:+,.2f}")
    return {
        "note": "measurement, not parameter selection; no strategy change",
        "equity": START_EQUITY,
        "limits": {"max_concurrent": max_conc, "cluster_cap": cluster_cap,
                   "max_entries_per_session": max_entries,
                   "min_seconds_between_entries": min_gap_s,
                   "buy_fee": buy_fee, "sell_fee": sell_fee},
        "n_signals": len(signals), "n_taken": n_closed,
        "n_open": len(still_open),
        "exits": reasons, "not_taken": {k: v for k, v in skipped.items()
                                        if k != "_signals"},
        "not_taken_detail": skipped.get("_signals", []),
        "realised_net_usd": net,
        "realised_pct_of_equity": 100.0 * net / START_EQUITY,
        "max_drawdown_usd": max_dd,
        "max_drawdown_pct_of_equity": 100.0 * max_dd / START_EQUITY,
        "trades": taken, "still_open": still_open,
        "cross_check_independent_trades_net": sum(t["net"] for t in d_trades),
    }


def part_g(trades):
    """Distribution of the stop breach at exit, in entry-ATR units."""
    stops = [t for t in trades if t["reason"] == "STOP_HIT"]
    rows = []
    for t in stops:
        breach = (t["stop"] - t["exit"]) / t["entry_atr"]
        rows.append({
            "symbol": t["symbol"], "date": t["entry_date"],
            "time_et": t["entry_et"], "entry": t["entry"],
            "stop": t["stop"], "exit": t["exit"], "entry_atr": t["entry_atr"],
            "breach_atr": breach,
        })
    print("\n\nPART G -- STOP EXIT DEPTH " + "-" * 71)
    print("For each STOP_HIT in Part D: how far below the stop level the exit")
    print("close actually was, in units of that trade's entry ATR20.")
    print("Close-based: the stop fires on a 5-min close at or below the level and")
    print("the exit is that close, so this is a lower bound on intrabar depth.")
    print("-" * 96 + "\n")
    if not rows:
        print("no STOP_HIT exits in the sample")
        return {"note": "measurement, not parameter selection", "n": 0}
    print(f"{'symbol':8}{'entry date/time':18}{'entry':>10}{'stop':>10}"
          f"{'exit':>10}{'ATR20':>8}{'breach ATR':>12}")
    for r in rows:
        print(f"{r['symbol']:8}{r['date'] + ' ' + r['time_et']:18}"
              f"{r['entry']:>10.4f}{r['stop']:>10.4f}{r['exit']:>10.4f}"
              f"{r['entry_atr']:>8.4f}{r['breach_atr']:>12.3f}")
    vals = sorted(r["breach_atr"] for r in rows)
    print(f"\nn={len(vals)}   min {vals[0]:.3f}   p25 {statistics.quantiles(vals, n=4)[0]:.3f}"
          f"   median {statistics.median(vals):.3f}"
          f"   p75 {statistics.quantiles(vals, n=4)[2]:.3f}   max {vals[-1]:.3f}"
          f"   mean {statistics.fmean(vals):.3f}")
    buckets = [("0 <= x < 0.05", 0.0, 0.05), ("0.05 <= x < 0.15", 0.05, 0.15),
               ("0.15 <= x < 0.30", 0.15, 0.30), ("0.30 <= x < 0.60", 0.30, 0.60),
               ("x >= 0.60", 0.60, float("inf"))]
    for label, lo, hi in buckets:
        n = sum(1 for v in vals if lo <= v < hi)
        print(f"  {label:20}{n:>3}  {'#' * n}")
    return {
        "note": "measurement, not parameter selection",
        "n": len(vals), "min": vals[0], "median": statistics.median(vals),
        "max": vals[-1], "mean": statistics.fmean(vals),
        "buckets": {label: sum(1 for v in vals if lo <= v < hi)
                    for label, lo, hi in buckets},
        "trades": rows,
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--extend", action="store_true",
                    help="also run PARTS E-G (filter ablation, sequential sim, "
                         "stop depth); Parts A-D output is unchanged either way")
    args = ap.parse_args(argv)
    cfg = yaml.safe_load((ROOT / "config" / "risk_limits.yaml").read_text())
    min_atr = {k: float(v) for k, v in cfg["fees"]["min_atr20_usd"].items()}

    merged = {s: load_merged(s) for s in ci.SYMBOLS}

    # Bad ticks: the committed rule, evaluated on the BASELINE window exactly as
    # compute_indicators.py evaluated it. Quarantine is PER SYMBOL -- a timestamp
    # is shared across symbols, so a union would delete a healthy bar from every
    # other symbol at the same minute.
    excluded: dict[str, set[int]] = {}
    flagged_all = {}
    for sym, bars in merged.items():
        window = [b for b in bars if BASELINE_FROM <= b["date"] <= BASELINE_TO]
        _, flagged = ci.find_bad_ticks(window)
        flagged_all[sym] = flagged
        excluded[sym] = {f["tk"] for f in flagged}

    series = {sym: ci.compute_series(
        ci.session_bars([dict(b) for b in bars if b["tk"] not in excluded[sym]]))
        for sym, bars in merged.items()}
    spy_regime = regime_series(series["SPY"])

    a = part_a(spy_regime)
    b = part_b(series, spy_regime, min_atr)
    c = part_c(spy_regime)

    print("=" * 96)
    print(f"Sample: {WINDOW_FROM}..{WINDOW_TO}, 5-min RTH bars 09:35-15:55 ET, "
          f"bad ticks excluded ({sum(len(v) for v in flagged_all.values())} bars)")
    for sym, fl in flagged_all.items():
        for f in fl:
            print(f"  excluded  {sym} {f['date']} {f['hhmm']} ET")
    print("=" * 96)

    # ---------------- PART A ----------------
    print("\nPART A -- REGIME AVAILABILITY (SPY close > SPY SMA60)\n")
    print(f"{'session':12}{'bars':>6}{'ok':>5}{'blocked':>9}{'% tradable':>12}"
          f"{'open':>9}{'close':>9}{'flips':>7}  first..last flip")
    for d, r in a["per_session"].items():
        flips = (f"{r['first_flip_et']}..{r['last_flip_et']}"
                 if r["transitions"] else "--")
        note = "" if d <= BASELINE_TO else "  (partial)"
        print(f"{d:12}{r['bars']:>6}{r['ok']:>5}{r['blocked']:>9}"
              f"{r['pct_tradable']:>11.1f}%{r['opens']:>9}{r['closes']:>9}"
              f"{r['transitions']:>7}  {flips}{note}")
    print(f"\nOVERALL (all {a['all_bars']} bars)      regime OK: {a['ok_bars']:>4}  "
          f"= {a['pct_ok_all']:.1f}%")
    print(f"Full sessions only (11-09..11-19)  regime OK: {a['full_session_ok']:>4}  "
          f"= {a['pct_ok_full']:.1f}%  of {a['full_session_bars']} bars")

    # ---------------- PART B ----------------
    print("\n\nPART B -- JOINT SIGNAL COUNT (dislocation + tick up + 10:00-15:00, "
          "then each gate)\n")
    hdr = (f"{'symbol':9}{'base':>7}{'ATR gate':>10}{'regime':>9}{'both':>7}"
           f"   {'removed by ATR':>16}{'removed by regime':>19}")
    print(hdr)
    totals = {"base": 0, "atr_only": 0, "regime_only": 0, "joint": 0}
    for sym, r in b.items():
        n = {k: len(r[k]) for k in ("base", "atr_only", "regime_only", "joint")}
        for k in totals:
            totals[k] += n[k]
        print(f"{sym:9}{n['base']:>7}{n['atr_only']:>10}{n['regime_only']:>9}"
              f"{n['joint']:>7}   {n['base'] - n['atr_only']:>16}"
              f"{n['base'] - n['regime_only']:>19}")
    print(f"{'TOTAL':9}{totals['base']:>7}{totals['atr_only']:>10}"
          f"{totals['regime_only']:>9}{totals['joint']:>7}   "
          f"{totals['base'] - totals['atr_only']:>16}"
          f"{totals['base'] - totals['regime_only']:>19}")

    base_full = sum(1 for r in b.values() for x in r["base"] if x["date"] <= BASELINE_TO)
    raw_series = {sym: ci.compute_series(ci.session_bars(list(bars)))
                  for sym, bars in merged.items()}
    b_raw = part_b(raw_series, spy_regime, min_atr)
    raw_full = sum(1 for r in b_raw.values() for x in r["base"]
                   if x["date"] <= BASELINE_TO)

    archived = json.loads((HERE / "archive" / "indicators_output.json").read_text())
    print(f"\nBaseline cross-check vs archive/indicators_output.json "
          f"({BASELINE_FROM}..{BASELINE_TO}, no regime, no ATR gate):")
    print(f"  {'symbol':8}{'clean now':>11}{'clean arch':>12}{'raw now':>9}{'raw arch':>10}")
    for sym in ci.SYMBOLS:
        c_now = sum(1 for x in b[sym]["base"] if x["date"] <= BASELINE_TO)
        r_now = sum(1 for x in b_raw[sym]["base"] if x["date"] <= BASELINE_TO)
        print(f"  {sym:8}{c_now:>11}{archived[sym]['clean']['qual_count']:>12}"
              f"{r_now:>9}{archived[sym]['raw']['qual_count']:>10}")
    print(f"  {'TOTAL':8}{base_full:>11}"
          f"{sum(archived[s]['clean']['qual_count'] for s in ci.SYMBOLS):>12}"
          f"{raw_full:>9}{sum(archived[s]['raw']['qual_count'] for s in ci.SYMBOLS):>10}")
    print(f"  quoted baseline 157 = raw sum (QQQ 29); clean sum is 158 (QQQ 30 -- "
          f"removing the 11-19 11:55 tick admits one extra QQQ bar).")
    print(f"  this script reproduces both: clean {'MATCH' if base_full == 158 else 'DIFF'}, "
          f"raw {'MATCH' if raw_full == 157 else 'DIFF'}")

    print("\nJoint signals per symbol per session:")
    days = sorted({x["date"] for r in b.values() for x in r["base"]})
    print("  " + f"{'session':12}" + "".join(f"{s:>8}" for s in ci.SYMBOLS) + f"{'tot':>7}")
    for d in days:
        row = [sum(1 for x in b[s]["joint"] if x["date"] == d) for s in ci.SYMBOLS]
        print("  " + f"{d:12}" + "".join(f"{v:>8}" for v in row) + f"{sum(row):>7}")
    print("  " + f"{'TOTAL':12}" + "".join(f"{len(b[s]['joint']):>8}" for s in ci.SYMBOLS)
          + f"{totals['joint']:>7}")

    # ---------------- PART C ----------------
    print("\n\nPART C -- PERSISTENCE: session closes blocked -> next session\n")
    if not c:
        print("  no session in the sample closed with SPY below SMA60")
    else:
        print(f"{'closed blocked':15}{'next session':14}{'bars in':>9}{'minutes':>9}"
              f"{'at ET':>8}   next session ok/bars")
        for r in c:
            if r["bars_into_session"] is None:
                print(f"{r['closed_blocked']:15}{r['next_session']:14}"
                      f"{'--':>9}{'--':>9}{'--':>8}   "
                      f"{r['next_session_ok_bars']}/{r['next_session_bars']} (never reopened)")
            else:
                print(f"{r['closed_blocked']:15}{r['next_session']:14}"
                      f"{r['bars_into_session']:>9}{r['minutes_into_session']:>9}"
                      f"{r['recovered_at_et']:>8}   "
                      f"{r['next_session_ok_bars']}/{r['next_session_bars']}")

    # ---------------- PART D ----------------
    print("\n\nPART D -- EXPLORATORY OUTCOME CHECK " + "-" * 52)
    print("THIS IS A 9-SESSION EXPLORATORY MEASUREMENT, NOT VALIDATION OF")
    print("PROFITABILITY. One share per signal, every signal treated as an")
    print("independent trade (no position cap, no cluster rule, no overlap")
    print("handling), fills assumed at the close of the bar that triggers.")
    print("-" * 96 + "\n")

    idx_by_sym = {sym: {bar["tk"]: i for i, bar in enumerate(bars)}
                  for sym, bars in series.items()}
    entries = [(sym, idx_by_sym[sym][x["tk"]])
               for sym, r in b.items() for x in r["joint"]]
    trades, open_trades, traces = [], [], []
    for sym, idx in entries:
        t = simulate(series[sym], idx)
        if t is None:
            open_trades.append((sym, series[sym][idx]))
            continue
        t["symbol"] = sym
        t["entry_date"] = series[sym][idx]["date"]
        t["entry_et"] = series[sym][idx]["hhmm"]
        trades.append(t)

    wins = [t for t in trades if t["net"] > 0]
    losses = [t for t in trades if t["net"] <= 0]
    gross_wins = [t for t in trades if t["gross"] > 0]
    print(f"{'symbol':9}{'entry':18}{'exit':18}{'reason':>13}"
          f"{'held':>7}{'gross':>9}{'net':>9}")
    for t in trades:
        print(f"{t['symbol']:9}{t['entry_date'] + ' ' + t['entry_et']:18}"
              f"{t['exit_date'] + ' ' + t['exit_et']:18}{t['reason']:>13}"
              f"{t['held_minutes']:>7.0f}{t['gross']:>9.2f}{t['net']:>9.2f}")
    for sym, e in open_trades:
        print(f"{sym:9}{e['date'] + ' ' + e['hhmm']:18}{'still open':18}"
              f"{'OPEN':>13}")

    print(f"\nn={len(trades)} closed ({len(open_trades)} still open at the data "
          f"edge, excluded)")
    if trades:
        print(f"win rate {100.0 * len(wins) / len(trades):.1f}%  "
              f"({len(wins)}W/{len(losses)}L)")
        print(f"mean win  {statistics.fmean(t['net'] for t in wins):+.2f}" if wins
              else "mean win  n/a")
        print(f"mean loss {statistics.fmean(t['net'] for t in losses):+.2f}" if losses
              else "mean loss n/a")
        print(f"total     {sum(t['net'] for t in trades):+.2f}  "
              f"(gross {sum(t['gross'] for t in trades):+.2f}, "
              f"fees {ROUND_TRIP_COST * len(trades):.2f})")
        print(f"before fees: {len(gross_wins)}/{len(trades)} closed above entry "
              f"(gross {'+'.join(format(t['gross'], '.2f') for t in gross_wins) or 'none'})"
              f" -- every one short of the {ROUND_TRIP_COST:.2f} round trip")

    # ---------------- arithmetic traces ----------------
    print("\n\nARITHMETIC TRACES " + "-" * 78)
    first_sym, first_sig = next(
        ((s, x) for s, r in b.items() for x in r["joint"]), (None, None))
    sym0 = first_sym
    if first_sig is None:
        print("\nno joint signal in the sample; nothing to trace")
    else:
        print(f"\nPart B signal, {sym0} {first_sig['date']} {first_sig['hhmm']} ET:")
        thr = first_sig["sma20"] - ci.DISLOCATION_ATR_MULT * first_sig["atr20"]
        print(f"  SMA20           = {first_sig['sma20']:.6f}")
        print(f"  ATR20 (Wilder)  = {first_sig['atr20']:.6f}")
        print(f"  threshold       = {first_sig['sma20']:.6f} - 1.5*{first_sig['atr20']:.6f}"
              f" = {thr:.6f}")
        print(f"  close           = {first_sig['c']:.4f}  -> below threshold by "
              f"{thr - first_sig['c']:.4f}")
        print(f"  prev close      = {first_sig['prev_close']:.4f}  -> ticked up by "
              f"{first_sig['c'] - first_sig['prev_close']:+.4f}")
        print(f"  bar start {first_sig['hhmm']} ET in [10:00, 15:00); "
              f"ATR20 {first_sig['atr20']:.6f} >= gate {min_atr[f'US.{sym0}']:.4f}; "
              f"regime {spy_regime_by_tk(spy_regime, first_sig['tk'])}")

    if trades:
        t = trades[0]
        print(f"\nPart D trade, {t['symbol']} entry {t['entry_date']} {t['entry_et']} ET:")
        print(f"  entry (bar close)   {t['entry']:.4f}")
        print(f"  stop  = entry - 1.0*ATR20 = {t['entry']:.4f} - {t['entry_atr']:.6f}"
              f" = {t['stop']:.6f}")
        print(f"  exit  {t['exit_date']} {t['exit_et']} ET at close {t['exit']:.4f} "
              f"({t['reason']})")
        print(f"  'target' at that bar = SMA20 {t['exit_sma20']:.6f}"
              f"  (close {'>=' if t['exit_sma20'] and t['exit'] >= t['exit_sma20'] else '<'} SMA20)")
        print(f"  held {t['held_minutes']:.0f} min (limit {TIME_STOP_MINUTES}; "
              f"flat-by 15:50)")
        print(f"  gross = {t['exit']:.4f} - {t['entry']:.4f} = {t['gross']:+.4f}")
        print(f"  net   = {t['gross']:+.4f} - {ROUND_TRIP_COST:.2f} = {t['net']:+.4f}")

    print("\n" + "-" * 96)
    print("Part D is an exploratory outcome check over the signals that survived")
    print("every gate in this sample. n is small, the sample is 9 sessions plus a")
    print("partial one, exits are modelled on close prices with no slippage beyond")
    print("the flat $1.99 fee, and every signal is treated as an independent trade.")
    print("It is an input to deciding what to keep measuring, not evidence that the")
    print("strategy makes money.")
    print("-" * 96)

    payload = {
        "generated_by": "analysis/measure_signal_rate.py",
        "sample": [WINDOW_FROM, WINDOW_TO],
        "part_a": a,
        "part_b": {
            sym: {
                "base": len(r["base"]), "atr_only": len(r["atr_only"]),
                "regime_only": len(r["regime_only"]), "joint": len(r["joint"]),
                "joint_per_session": r["per_session"],
                "base_per_session": r["base_per_session"],
            } for sym, r in b.items()
        },
        "part_b_totals": totals,
        "part_b_baseline_1119": base_full,
        "part_c": c,
        "part_d": {
            "disclaimer": "9-session exploratory measurement, not validation of "
                          "profitability. 1 share, independent trades, close fills, "
                          "$1.99 round trip.",
            "n_closed": len(trades), "n_open": len(open_trades),
            "wins": len(wins), "losses": len(losses),
            "gross_wins": len(gross_wins),
            "win_rate_pct": 100.0 * len(wins) / len(trades) if trades else None,
            "mean_win": statistics.fmean(t["net"] for t in wins) if wins else None,
            "mean_loss": statistics.fmean(t["net"] for t in losses) if losses else None,
            "total_net": sum(t["net"] for t in trades),
            "trades": trades,
        },
    }
    OUT.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"\nWrote {OUT}")

    if not args.extend:
        return 0

    e = part_e(b, series, idx_by_sym, spy_regime, min_atr, trades, open_trades)
    f = part_f(b, series, ROOT / "config" / "risk_limits.yaml", trades)
    g = part_g(trades)

    print("\n" + "-" * 96)
    print("PARTS E-G ARE MEASUREMENT, NOT PARAMETER SELECTION. Nothing here")
    print("was used to pick a threshold, and no strategy change has been made:")
    print("the regime rule, ATR floor, stops and limits in config/ are exactly")
    print("what was pre-committed. These numbers describe this 9-session sample")
    print("plus one partial session and do not establish that any variant would")
    print("keep behaving the same way out of sample.")
    print("-" * 96)

    ext = {
        "generated_by": "analysis/measure_signal_rate.py --extend",
        "sample": [WINDOW_FROM, WINDOW_TO],
        "disclaimer": "Parts E-G are measurement, not parameter selection. No "
                      "strategy change has been made.",
        "part_e": e,
        "part_f": f,
        "part_g": g,
    }
    ext_out = HERE / "signal_rate_extended.json"
    ext_out.write_text(json.dumps(ext, indent=2) + "\n")
    print(f"\nWrote {ext_out}")
    return 0


def spy_regime_by_tk(spy_regime: list[dict], tk: int) -> str:
    for b in spy_regime:
        if b["tk"] == tk:
            return "OK" if b["regime_ok"] else "BLOCKED"
    return "n/a"


if __name__ == "__main__":
    raise SystemExit(main())
