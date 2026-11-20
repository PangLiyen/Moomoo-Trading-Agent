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

DEFINITIONS are imported from analysis/compute_indicators.py rather than
restated, so a drift in one cannot silently disagree with the other: 5-minute
RTH bars 09:35..15:55 ET (16:00 print dropped), bad-tick quarantine at
10x median range / 1% wick, Wilder ATR20 seeded on the first 20 TRs, SMA20
inclusive of the current close, entry window 10:00 <= t < 15:00 ET.

Run:  python3 analysis/measure_signal_rate.py
Also writes: analysis/signal_rate_output.json
"""

from __future__ import annotations

import json
import statistics
import sys
from datetime import datetime, timedelta
from pathlib import Path

import yaml

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))

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
# Main
# ---------------------------------------------------------------------------

def main() -> int:
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

    OUT.write_text(json.dumps({
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
    }, indent=2) + "\n")
    print(f"\nWrote {OUT}")
    return 0


def spy_regime_by_tk(spy_regime: list[dict], tk: int) -> str:
    for b in spy_regime:
        if b["tk"] == tk:
            return "OK" if b["regime_ok"] else "BLOCKED"
    return "n/a"


if __name__ == "__main__":
    raise SystemExit(main())
