#!/usr/bin/env python3
"""Compare the three SMA20/ATR20 implementations bar by bar.

  a) engine.signal.compute_indicators  -- imported, called on the bar prefix
     ending at each bar (it is a snapshot function, not a series function)
  b) analysis/compute_indicators.py    -- compute_series() over the full series
  c) analysis/canonical.mjs            -- via node, if it emits per-bar values

All three are fed the SAME cleaned bars: archive + live merged, 09:35..15:55 ET,
with the committed bad-tick exclusion set (3-rule test on 11-09..11-19) applied
once. That isolates the indicator arithmetic from the exclusion policy, which is
compared separately on the raw bars.

Anything above 1e-9 absolute is reported as a defect with the arithmetic behind
it. Nothing is fixed here; no config or engine value is touched.

Run:  python3 analysis/verify_implementations.py
Also writes: analysis/verify_implementations_output.json
"""

from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(ROOT))

import compute_indicators as ci  # noqa: E402
import measure_signal_rate as msr  # noqa: E402
from engine import signal as esig  # noqa: E402

TOL = 1e-9
WINDOW_FROM = msr.WINDOW_FROM       # 2026-11-09
WINDOW_TO = msr.WINDOW_TO           # 2026-11-20 (partial)
OUT = HERE / "verify_implementations_output.json"


def engine_bar(b: dict) -> esig.Bar:
    return esig.Bar(
        ts_et=datetime.fromtimestamp(b["tk"] / 1000, ci.ET),
        open=b["o"], high=b["h"], low=b["l"], close=b["c"],
        volume=float(b["v"]),
    )


def canonical_probe(sym: str) -> dict:
    """Run canonical.mjs and report whether it exposes a per-bar series."""
    try:
        proc = subprocess.run(
            ["node", str(HERE / "canonical.mjs"), sym,
             f"archive/bars_US_{sym}_full.json"],
            capture_output=True, text=True, timeout=120,
        )
    except FileNotFoundError:
        return {"ran": False, "reason": "node not found on PATH"}
    except subprocess.TimeoutExpired:
        return {"ran": False, "reason": "node timed out"}
    if proc.returncode != 0:
        return {"ran": False, "reason": f"node exit {proc.returncode}: "
                                        f"{proc.stderr.strip()[:200]}"}
    try:
        doc = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"ran": False, "reason": "stdout was not JSON"}
    keys = sorted(doc)
    per_bar_keys = []
    kept = doc.get("bars_total_kept")
    for k, v in doc.items():
        if isinstance(v, list) and kept and len(v) == kept:
            per_bar_keys.append(k)
    return {"ran": True, "keys": keys, "per_bar_keys": per_bar_keys,
            "outliers": doc.get("outliers", [])}


def load_clean(sym: str):
    raw = ci.session_bars([dict(b) for b in msr.load_merged(sym)])
    window = [b for b in raw if msr.BASELINE_FROM <= b["date"] <= msr.BASELINE_TO]
    _, flagged = ci.find_bad_ticks(window)
    excluded = {f["tk"] for f in flagged}
    clean = [b for b in raw if b["tk"] not in excluded]
    return raw, window, flagged, clean


def compare_symbol(sym: str, canon: dict) -> dict:
    raw, window, b_flags, clean = load_clean(sym)
    b_series = ci.compute_series([dict(b) for b in clean])
    e_bars = [engine_bar(b) for b in clean]

    # a) engine snapshots, one per bar (prefix ending at that bar)
    eng: list = [None] * len(clean)
    prefix_excl: set[str] = set()
    for i in range(20, len(clean)):
        ind = esig.compute_indicators(sym, e_bars[:i + 1])
        eng[i] = ind
        if ind.excluded_outliers:
            prefix_excl.update(ind.excluded_outliers)

    sma_d, atr_d = [], []
    atr_b_none = []
    for i, ind in enumerate(eng):
        if ind is None:
            continue
        b = b_series[i]
        if b["sma20"] is not None:
            sma_d.append((abs(ind.sma20 - b["sma20"]), i,
                          ind.sma20, b["sma20"]))
        if b["atr20"] is None:
            atr_b_none.append(i)
        else:
            atr_d.append((abs(ind.atr20 - b["atr20"]), i,
                          ind.atr20, b["atr20"]))

    def top(rows):
        if not rows:
            return None
        d, i, va, vb = max(rows, key=lambda r: r[0])
        return {"abs_diff": d, "index": i,
                "date": b_series[i]["date"], "time_et": b_series[i]["hhmm"],
                "time_key": b_series[i]["tk"],
                "engine": va, "compute_indicators": vb}

    sma_top = top(sma_d)
    atr_top = top(atr_d)
    over = [r for r in atr_d if r[0] > TOL]
    over_sma = [r for r in sma_d if r[0] > TOL]
    atr_over_range = (None if not over else
                      (b_series[min(r[1] for r in over)]["date"],
                       b_series[max(r[1] for r in over)]["date"],
                       len(over)))

    b_set = {f["tk"] for f in b_flags}
    raw_e_bars = [engine_bar(x) for x in raw]
    e_idx = set(esig.flag_outlier_bars(raw_e_bars))
    e_set = {raw[i]["tk"] for i in e_idx}
    c_set = set()
    if canon.get("ran"):
        c_set = {f"{o['d']} {o['t']}" for o in canon.get("outliers", [])}
    b_str = {f"{f['date']} {f['hhmm']}" for f in b_flags}
    e_str = {f"{raw[i]['date']} {raw[i]['hhmm']}" for i in e_idx}
    e_win_str = {f"{raw[i]['date']} {raw[i]['hhmm']}" for i in e_idx
                 if msr.BASELINE_FROM <= raw[i]["date"] <= msr.BASELINE_TO}

    # entry rule, same three conditions evaluate_entry owns; regime/quote/spread
    # forced open so the comparison is about the dislocation arithmetic only
    eng_qual, b_qual = [], []
    for i, ind in enumerate(eng):
        if ind is None:
            continue
        b = b_series[i]
        if not (WINDOW_FROM <= b["date"] <= WINDOW_TO):
            continue
        v = esig.evaluate_entry(ind, regime_ok=True, quote_age_seconds=0.0,
                                spread_pct_of_mid=0.0, now_et=ind.as_of_et)
        if v.qualifies:
            eng_qual.append(b["tk"])
        if (b["sma20"] is not None and b["atr20"] is not None
                and b["prev_close"] is not None
                and ci.ENTRY_WINDOW_START_MIN <= b["mins"] < ci.ENTRY_WINDOW_END_MIN
                and b["c"] < b["sma20"] - ci.DISLOCATION_ATR_MULT * b["atr20"]
                and b["c"] > b["prev_close"]):
            b_qual.append(b["tk"])

    eng_only = sorted(set(eng_qual) - set(b_qual))
    b_only = sorted(set(b_qual) - set(eng_qual))

    def bar_at(tk):
        i = next(j for j, x in enumerate(b_series) if x["tk"] == tk)
        x = b_series[i]
        return {"date": x["date"], "time_et": x["hhmm"], "close": x["c"],
                "sma20": x["sma20"], "atr20": x["atr20"],
                "engine_atr20": (eng[i].atr20 if eng[i] else None),
                "prev_close": x["prev_close"]}

    return {
        "symbol": sym,
        "bars": len(clean),
        "sma": {"max": sma_top, "n_over_tol": len(over_sma)},
        "atr": {"max": atr_top, "n_over_tol": len(over),
                "over_tol_range": atr_over_range,
                "compute_indicators_none_at": [b_series[i]["date"] + " "
                                               + b_series[i]["hhmm"]
                                               for i in atr_b_none]},
        "prefix_exclusions": sorted(prefix_excl),
        "exclusion": {
            "committed": sorted(b_str),
            "engine": sorted(e_str),
            "canonical": sorted(c_set),
            "agree_ab": b_str == e_win_str,
            "agree_ac": (None if not canon.get("ran") else b_str == c_set),
        },
        "entry": {"engine": len(eng_qual), "compute": len(b_qual),
                  "engine_only": [bar_at(tk) for tk in eng_only],
                  "compute_only": [bar_at(tk) for tk in b_only]},
        "_b_series": b_series, "_eng": eng, "_clean": clean,
    }


def main() -> int:
    print("=" * 100)
    print("IMPLEMENTATION VERIFICATION -- SMA20 / ATR20 / entry rule")
    print("=" * 100)
    print("Input: archive + live merged bars, 09:35..15:55 ET, committed bad-tick")
    print("exclusions applied once. Engine is called on the prefix ending at each")
    print("bar; compute_indicators.py on the full series. Tolerance 1e-9 absolute.")

    canon_spy = canonical_probe("SPY")
    if canon_spy.get("ran"):
        print(f"\ncanonical.mjs ran. Top-level keys: {', '.join(canon_spy['keys'])}")
        if canon_spy["per_bar_keys"]:
            print(f"per-bar arrays: {canon_spy['per_bar_keys']}")
        else:
            print("canonical.mjs emits NO per-bar SMA20/ATR20 series: its only")
            print("bar-level output is qual_bars (signal subset, rounded to 4 dp)")
            print("and outliers. Per the brief, the numeric comparison is")
            print("engine vs compute_indicators only; canonical is still used")
            print("for the bad-tick exclusion and signal-count checks.")
    else:
        print(f"\ncanonical.mjs did not run: {canon_spy.get('reason')}")

    payload = {"generated_by": "analysis/verify_implementations.py",
               "tolerance": TOL, "canonical": None, "symbols": {}}
    results = {}
    for sym in ci.SYMBOLS:
        canon = canon_spy if sym == "SPY" else canonical_probe(sym)
        r = compare_symbol(sym, canon)
        results[sym] = r

        print(f"\n\n{sym}   bars {r['bars']}   prefix exclusions beyond the "
              f"committed set: {len(r['prefix_exclusions'])}")
        sm, at = r["sma"]["max"], r["atr"]["max"]
        print(f"  SMA20  max |engine - compute| = {sm['abs_diff']:.3e}  at "
              f"{sm['date']} {sm['time_et']} ET   (bars > 1e-9: "
              f"{r['sma']['n_over_tol']})")
        print(f"  ATR20  max |engine - compute| = {at['abs_diff']:.3e}  at "
              f"{at['date']} {at['time_et']} ET   (bars > 1e-9: "
              f"{r['atr']['n_over_tol']})")
        if r["atr"]["over_tol_range"]:
            lo, hi, n = r["atr"]["over_tol_range"]
            print(f"         > 1e-9 spans {lo} .. {hi}  ({n} bars)")
        if r["atr"]["compute_indicators_none_at"]:
            print("         compute_indicators reports atr20=None at: "
                  + ", ".join(r["atr"]["compute_indicators_none_at"]))
        ex = r["exclusion"]
        print(f"  bad ticks  committed {ex['committed'] or 'none'}  engine "
              f"{ex['engine'] or 'none'}  canonical {ex['canonical'] or 'none'}"
              f"   a==b: {ex['agree_ab']}  a==c: {ex['agree_ac']}")
        en = r["entry"]
        print(f"  entry      engine {en['engine']}  compute {en['compute']}  "
              f"engine-only {len(en['engine_only'])}  compute-only "
              f"{len(en['compute_only'])}")

    # ---- the seed each implementation uses, on SPY ----
    spy = results["SPY"]
    b = spy["_b_series"]
    tr0, tr20 = b[0]["tr"], b[20]["tr"]
    seed_b = sum(x["tr"] for x in b[0:20]) / 20
    seed_e = sum(x["tr"] for x in b[1:21]) / 20
    d = seed_e - seed_b
    obs21 = abs(spy["_eng"][21].atr20 - b[21]["atr20"])
    atr20_b = b[20]["atr20"]
    print("\n" + "-" * 100)
    print(f"ATR SEED ARITHMETIC (SPY) -- compute_indicators mode: {ci.ATR_SEED_MODE}")
    print("-" * 100)
    print(f"  TR[0]  ({b[0]['date']} {b[0]['hhmm']} ET) = H-L only, first bar in "
          f"file = {tr0:.6f}  (not a true range)")
    print(f"  TR[20] ({b[20]['date']} {b[20]['hhmm']} ET) = {tr20:.6f}")
    print(f"  legacy-port seed  mean(TR[0..19]) = {seed_b:.6f}")
    print(f"  engine seed       mean(TR[1..20]) = {seed_e:.6f}")
    print(f"  difference        {d:+.6f}, decaying as (19/20)^n")
    print(f"  compute_indicators ATR20 at index 20 = {atr20_b}  "
          f"(engine = {spy['_eng'][20].atr20:.6f})")
    print(f"  |engine - compute| at index 21 = {obs21:.3e}")

    # ---- entry counts vs signal_rate_output.json ----
    sr = json.loads((HERE / "signal_rate_output.json").read_text())
    print("\n\nENTRY RULE vs analysis/signal_rate_output.json (part_b base)")
    print("-" * 100)
    print(f"{'symbol':9}{'engine':>8}{'compute':>9}{'json':>7}   status")
    total = {"engine": 0, "compute": 0, "json": 0}
    for sym in ci.SYMBOLS:
        e = results[sym]["entry"]["engine"]
        c = results[sym]["entry"]["compute"]
        j = sr["part_b"][sym]["base"]
        total["engine"] += e
        total["compute"] += c
        total["json"] += j
        ok = (e == c == j)
        print(f"{sym:9}{e:>8}{c:>9}{j:>7}   {'MATCH' if ok else 'DIFF'}")
    print(f"{'TOTAL':9}{total['engine']:>8}{total['compute']:>9}"
          f"{total['json']:>7}   "
          f"{'MATCH' if len(set(total.values())) == 1 else 'DIFF'}")

    diffs = [(s, x) for s in ci.SYMBOLS for key in ("engine_only", "compute_only")
             for x in results[s]["entry"][key]]
    if diffs:
        print("\nDiffering bars:")
        for s, x in diffs:
            print(f"  {s} {x['date']} {x['time_et']} ET  close {x['close']:.4f} "
                  f"sma {x['sma20']:.6f} atr(c) {x['atr20']:.6f} "
                  f"atr(a) {x['engine_atr20']:.6f} prev {x['prev_close']:.4f}")

    print("\n" + "-" * 100)
    print("WHAT THIS SHOWS")
    print("Numeric agreement is stated per series above; anything above 1e-9 is")
    print("called a defect with the arithmetic printed. The entry comparison")
    print("forces regime/quote/spread open because evaluate_entry's other")
    print("conditions are not about the indicator arithmetic. No file was")
    print("changed; fixes are for the owner to apply.")
    print("-" * 100)

    for r in results.values():
        r.pop("_b_series", None)
        r.pop("_eng", None)
        r.pop("_clean", None)
    payload["canonical"] = canon_spy
    payload["symbols"] = results
    payload["entry_totals"] = total
    OUT.write_text(json.dumps(payload, indent=2, default=str) + "\n")
    print(f"\nWrote {OUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
