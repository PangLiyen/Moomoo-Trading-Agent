#!/usr/bin/env python3
"""Reproduce the Session 00 5-minute indicator measurements (docs/API_CAPABILITIES.md s3d).

Reads analysis/bars_US_<SYM>_full.json and recomputes, per symbol:
  current SMA20 / ATR20, ATR20 min-median-max over the window, average bar-to-bar
  move, and the count of dislocation bars meeting the STRATEGY.md long-entry
  conditions 1+2 inside the 10:00-15:00 ET window.

Run from anywhere:   python3 analysis/compute_indicators.py
Also writes:         analysis/indicators_output.json

INPUT BAR FORMAT (quote_history_kline, ktype=6 five-minute, extended_time=0):
    [{"time_key": <ms epoch>, "date": 20261119,
      "open": f, "high": f, "low": f, "close": f, "volume": i}, ...]

DEFINITIONS -- fixed, do not tune to results.
  1. Session = 09:35..15:55 ET inclusive. The 16:00 bar is the closing print, not
     a tradeable 5-minute bar, and is dropped. Full sessions therefore have 77
     bars (XLE data has no 16:00 print at all, so it is 77 either way).
  2. Analysis window = 2026-11-09 .. 2026-11-19. The feed contains no 2026-11-20
     bars, so "current" below means the 2026-11-19 15:55 ET bar. Bars from
     2026-11-04..06 are kept purely as warm-up so ATR20/SMA20 are fully seeded.
  3. TR_t    = max(H-L, |H - prev_close|, |L - prev_close|)
     The first bar of each session compares against the previous session's 15:55
     close, so the overnight gap is inside TR. It is NOT reset at 09:35.
  4. ATR20   = Wilder RMA, NOT a simple 20-bar mean:
                 seed  = arithmetic mean of TR[1..20]
                 ATR_t = (ATR_{t-1} * 19 + TR_t) / 20
     TR[0] has no prior close -- its fallback is H-L, not a true range -- so it
     is excluded from the seed. The first ATR is emitted at input index 20 and
     the recursion starts at index 21 with TR[21], matching engine/signal.py
     atr_wilder() and analysis/canonical.mjs.
     The original port seeded on TR[0..19], left index 20 undefined, and never
     applied TR[20]; that behaviour is kept behind
     COMPUTE_INDICATORS_ATR_SEED=legacy_tr0_19 so the committed
     analysis/signal_rate_output.json stays reproducible.
  5. SMA20   = simple mean of the last 20 closes, current bar inclusive.
  6. Qualifier counts bars in the window where ALL of:
                 close < SMA20 - 1.5*ATR20
                 close > previous bar's close
                 10:00 <= bar start < 15:00 ET      (15:00 bar excluded)

BAD-TICK EXCLUSION -- explicit rule, not ad hoc.
  Within each symbol's window, med_range = median(H-L) of the kept bars. A bar is
  QUARANTINED if any of:
      (a) H-L > 10 * med_range          -- range blow-up vs its own history
      (b) H > max(O,C) * 1.01           -- wick the trade cannot have printed
      (c) L < min(O,C) * 0.99
  Two bars trip (a) on 2026-11-19, each ~16x its own symbol's median range:
      US.SPY  2026-11-19 15:55 ET  O 717.14  H 731.89  L 716.84  C 717.56   (16.01x)
      US.QQQ  2026-11-19 11:55 ET  O 662.49  H 676.49  L 654.20  C 658.27   (16.63x)
  Both are excluded from the "clean" variant. The raw variant (as published, no
  filtering) is printed beside it so the size of the effect is visible rather
  than asserted. Nothing is deleted from the source files: exclusion is a
  computation-time filter, so the published data stays auditable.

  Why both variants are reported: the SPY bad tick lands on the FINAL bar, so it
  shifts the headline ATR20 that would be read at decision time (2.3523 raw vs
  1.6840 clean). The strategy's cost gate uses ATR20 per trade, so which number
  you use changes whether a trade is refused. Hiding that would be the interesting
  part, not the fix.
"""

import json
import os
import statistics
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
OUT = HERE / "indicators_output.json"

SYMBOLS = ["SPY", "QQQ", "IWM", "XLF", "XLE"]
ET = ZoneInfo("America/New_York")

SESSION_START_MIN = 9 * 60 + 35      # 09:35 ET, first 5-minute bar
SESSION_END_MIN = 15 * 60 + 55       # 15:55 ET, last tradeable bar (16:00 print dropped)
WINDOW_FROM = "2026-11-09"
WINDOW_TO = "2026-11-19"

ENTRY_WINDOW_START_MIN = 10 * 60     # 10:00 ET
ENTRY_WINDOW_END_MIN = 15 * 60       # 15:00 ET, exclusive

ATR_PERIOD = 20
SMA_PERIOD = 20
DISLOCATION_ATR_MULT = 1.5

ATR_SEED_TR1_20 = "tr1_20"          # engine + canonical.mjs
ATR_SEED_LEGACY = "legacy_tr0_19"   # original port, pre-fix
ATR_SEED_MODE = os.environ.get("COMPUTE_INDICATORS_ATR_SEED", ATR_SEED_TR1_20)

RANGE_BLOWUP_MULT = 10.0
WICK_HIGH_MULT = 1.01
WICK_LOW_MULT = 0.99


def load_bars(symbol):
    path = HERE / "archive" / f"bars_US_{symbol}_full.json"
    if not path.exists():
        path = HERE / f"bars_US_{symbol}_full.json"
    raw = json.loads(path.read_text())
    bars = []
    for b in raw:
        ts = datetime.fromtimestamp(b["time_key"] / 1000, ET)
        bars.append(
            {
                "tk": b["time_key"],
                "date": ts.strftime("%Y-%m-%d"),
                "hhmm": ts.strftime("%H:%M"),
                "mins": ts.hour * 60 + ts.minute,
                "o": b["open"],
                "h": b["high"],
                "l": b["low"],
                "c": b["close"],
                "v": b["volume"],
            }
        )
    bars.sort(key=lambda b: b["tk"])
    deduped = {}
    for b in bars:
        deduped[b["tk"]] = b
    return [deduped[k] for k in sorted(deduped)]


def session_bars(bars):
    # Drops the 16:00 closing print; keeps 09:35..15:55.
    return [b for b in bars if SESSION_START_MIN <= b["mins"] <= SESSION_END_MIN]


def compute_series(bars, *, atr_seed=None):
    """Returns a new list of dicts with tr / atr20 / sma20 attached. Input untouched.

    atr_seed: None uses the module default from COMPUTE_INDICATORS_ATR_SEED
    ("tr1_20" unless overridden). Pass "legacy_tr0_19" to reproduce the
    original port exactly.
    """
    mode = atr_seed or ATR_SEED_MODE
    if mode not in (ATR_SEED_TR1_20, ATR_SEED_LEGACY):
        raise ValueError(f"unknown ATR seed mode: {mode!r}")

    out = []
    prev_close = None
    atr = None
    seed_sum = 0.0
    seed_n = 0
    closes = []

    for i, src in enumerate(bars):
        b = dict(src)
        if prev_close is None:
            b["tr"] = b["h"] - b["l"]
        else:
            b["tr"] = max(b["h"] - b["l"], abs(b["h"] - prev_close), abs(b["l"] - prev_close))
        b["prev_close"] = prev_close

        if mode == ATR_SEED_LEGACY:
            # Original port: seed on TR[0..19], first ATR at index 21, TR[20] unused.
            if i <= ATR_PERIOD:
                seed_sum += b["tr"]
                seed_n += 1
                if seed_n == ATR_PERIOD:
                    atr = seed_sum / ATR_PERIOD
                    b["atr20"] = atr
                else:
                    b["atr20"] = None
            else:
                atr = (atr * (ATR_PERIOD - 1) + b["tr"]) / ATR_PERIOD
                b["atr20"] = atr
        else:
            # Engine/canonical: TR[0] has no prior close, so the seed is TR[1..20].
            if i == 0:
                b["atr20"] = None
            elif i <= ATR_PERIOD:
                seed_sum += b["tr"]
                seed_n += 1
                if seed_n == ATR_PERIOD:
                    atr = seed_sum / ATR_PERIOD
                    b["atr20"] = atr
                else:
                    b["atr20"] = None
            else:
                atr = (atr * (ATR_PERIOD - 1) + b["tr"]) / ATR_PERIOD
                b["atr20"] = atr

        closes.append(b["c"])
        b["sma20"] = statistics.fmean(closes[-SMA_PERIOD:]) if len(closes) >= SMA_PERIOD else None

        out.append(b)
        prev_close = b["c"]
    return out


def find_bad_ticks(bars_in_window):
    med_range = statistics.median(b["h"] - b["l"] for b in bars_in_window)
    flagged = []
    for b in bars_in_window:
        rng = b["h"] - b["l"]
        reasons = []
        if rng > RANGE_BLOWUP_MULT * med_range:
            reasons.append(f"range {rng:.2f} = {rng / med_range:.2f}x median {med_range:.2f}")
        if b["h"] > max(b["o"], b["c"]) * WICK_HIGH_MULT:
            reasons.append("high wick > 1% above body top")
        if b["l"] < min(b["o"], b["c"]) * WICK_LOW_MULT:
            reasons.append("low wick > 1% below body bottom")
        if reasons:
            flagged.append({"date": b["date"], "hhmm": b["hhmm"], "tk": b["tk"], "bar": b, "reasons": reasons})
    return med_range, flagged


def summarize(symbol, series):
    in_window = [b for b in series if WINDOW_FROM <= b["date"] <= WINDOW_TO]
    usable = [b for b in in_window if b["atr20"] is not None and b["sma20"] is not None]
    if not usable:
        raise SystemExit(f"{symbol}: no bars with a seeded ATR20/SMA20 in window")

    cur = usable[-1]
    atrs = [b["atr20"] for b in usable]

    mov_with_gap = []
    mov_intraday = []
    for i, b in enumerate(usable):
        if b["prev_close"] is None:
            continue
        mov_with_gap.append(abs(b["c"] - b["prev_close"]))
        # First bar of a session has no same-session predecessor; exclude for intraday.
        if i > 0 and usable[i - 1]["date"] == b["date"]:
            mov_intraday.append(abs(b["c"] - b["prev_close"]))

    qual = []
    for b in usable:
        if b["prev_close"] is None:
            continue
        if not (ENTRY_WINDOW_START_MIN <= b["mins"] < ENTRY_WINDOW_END_MIN):
            continue
        threshold = b["sma20"] - DISLOCATION_ATR_MULT * b["atr20"]
        if b["c"] < threshold and b["c"] > b["prev_close"]:
            qual.append(b)

    per_day = {}
    for b in qual:
        per_day[b["date"]] = per_day.get(b["date"], 0) + 1

    days = sorted({b["date"] for b in usable})
    return {
        "bars_in_window": len(in_window),
        "bars_usable": len(usable),
        "first_bar": f"{usable[0]['date']} {usable[0]['hhmm']}",
        "last_bar": f"{cur['date']} {cur['hhmm']}",
        "last_close": cur["c"],
        "sma20": cur["sma20"],
        "atr20": cur["atr20"],
        "atr20_pct_of_price": 100.0 * cur["atr20"] / cur["c"],
        "atr20_min": min(atrs),
        "atr20_median": statistics.median(atrs),
        "atr20_max": max(atrs),
        "avg_move_with_gap_usd": statistics.fmean(mov_with_gap),
        "avg_move_with_gap_pct": 100.0 * statistics.fmean(mov_with_gap) / cur["c"],
        "avg_move_intraday_usd": statistics.fmean(mov_intraday),
        "avg_move_intraday_pct": 100.0 * statistics.fmean(mov_intraday) / cur["c"],
        "qual_count": len(qual),
        "qual_per_day": {d: per_day.get(d, 0) for d in days},
    }


def main():
    report = {}
    print("=" * 78)
    print("5-minute RTH bars, session 09:35-15:55 ET, window "
          f"{WINDOW_FROM}..{WINDOW_TO} (warm-up from 2026-11-04)")
    print(f"ATR seed mode: {ATR_SEED_MODE}  "
          "(set COMPUTE_INDICATORS_ATR_SEED=legacy_tr0_19 for the pre-fix port)")
    print("=" * 78)

    for symbol in SYMBOLS:
        bars = session_bars(load_bars(symbol))
        base_series = compute_series(bars)
        window = [b for b in bars if WINDOW_FROM <= b["date"] <= WINDOW_TO]

        med_range, flagged = find_bad_ticks(window)
        excluded_tks = {f["tk"] for f in flagged}

        # Clean variant: clone the bars before dropping, so the base series is
        # not mutated by the exclusion.
        clean_bars = [dict(b) for b in bars if b["tk"] not in excluded_tks]
        clean_series = compute_series(clean_bars)

        raw = summarize(symbol, base_series)
        clean = summarize(symbol, clean_series)

        report[symbol] = {
            "raw": raw,
            "clean": clean,
            "median_bar_range_usd": med_range,
            "excluded_bars": [
                {
                    "date": f["date"],
                    "time_et": f["hhmm"],
                    "time_key": f["tk"],
                    "open": f["bar"]["o"],
                    "high": f["bar"]["h"],
                    "low": f["bar"]["l"],
                    "close": f["bar"]["c"],
                    "volume": f["bar"]["v"],
                    "reasons": f["reasons"],
                }
                for f in flagged
            ],
        }

        print(f"\n{symbol}   bars: {raw['bars_in_window']} in window "
              f"({raw['first_bar']} -> {raw['last_bar']})")
        print(f"  {'':22}{'raw (as published)':>22}{'clean (ticks excluded)':>26}")
        rows = [
            ("last close", f"{raw['last_close']:.4f}", f"{clean['last_close']:.4f}"),
            ("SMA20", f"{raw['sma20']:.4f}", f"{clean['sma20']:.4f}"),
            ("ATR20", f"{raw['atr20']:.4f}", f"{clean['atr20']:.4f}"),
            ("ATR20 % of price", f"{raw['atr20_pct_of_price']:.3f}%", f"{clean['atr20_pct_of_price']:.3f}%"),
            ("ATR20 min", f"{raw['atr20_min']:.4f}", f"{clean['atr20_min']:.4f}"),
            ("ATR20 median", f"{raw['atr20_median']:.4f}", f"{clean['atr20_median']:.4f}"),
            ("ATR20 max", f"{raw['atr20_max']:.4f}", f"{clean['atr20_max']:.4f}"),
            ("avg move $ (with gap)", f"{raw['avg_move_with_gap_usd']:.4f}",
             f"{clean['avg_move_with_gap_usd']:.4f}"),
            ("avg move % (with gap)", f"{raw['avg_move_with_gap_pct']:.4f}%",
             f"{clean['avg_move_with_gap_pct']:.4f}%"),
            ("avg move $ (intraday)", f"{raw['avg_move_intraday_usd']:.4f}",
             f"{clean['avg_move_intraday_usd']:.4f}"),
            ("avg move % (intraday)", f"{raw['avg_move_intraday_pct']:.4f}%",
             f"{clean['avg_move_intraday_pct']:.4f}%"),
            ("qualifier bars", str(raw["qual_count"]), str(clean["qual_count"])),
        ]
        for label, a, b in rows:
            print(f"  {label:22}{a:>22}{b:>26}")

        # Per-day qualifier rows: print whichever variant the excluded tick touched.
        day_sets = sorted(set(raw["qual_per_day"]) | set(clean["qual_per_day"]))
        print("  qualifier bars per day   raw: "
              + ", ".join(f"{d[5:]}={raw['qual_per_day'].get(d, 0)}" for d in day_sets))
        print("                           clean: "
              + ", ".join(f"{d[5:]}={clean['qual_per_day'].get(d, 0)}" for d in day_sets))

        if flagged:
            print("  excluded bad ticks (rule above):")
            for f in report[symbol]["excluded_bars"]:
                print(f"    {f['date']} {f['time_et']} ET  O {f['open']}  H {f['high']}  "
                      f"L {f['low']}  C {f['close']}  v {f['volume']}")
                print(f"      -> {'; '.join(f['reasons'])}")

    caveats = (
        "9 sessions, 77 5-minute bars each. No 2026-11-20 bars existed at measurement time. "
        "ATR20 is Wilder, session-aware (TR carries the overnight gap). "
        "Qualifier = STRATEGY.md conditions 1+2 only; it does not apply the regime filter, "
        "spread gate, cluster/position budgets or the per-trade cost gate, so it is an upper "
        "bound on admissible setups, not a trade count."
    )
    print("\n" + "-" * 78)
    print(caveats)
    print("-" * 78)

    report["_meta"] = {
        "generated_by": "analysis/compute_indicators.py",
        "window": [WINDOW_FROM, WINDOW_TO],
        "session_bars_et": "09:35-15:55 (16:00 closing print dropped)",
        "atr_seed_mode": ATR_SEED_MODE,
        "atr": ("Wilder RMA, 20-period, seed = mean of TR[1..20], "
                "first value at input index 20, recursion from 21 (engine semantics)"
                if ATR_SEED_MODE == ATR_SEED_TR1_20 else
                "Wilder RMA, 20-period, seed = mean of TR[0..19], first value at "
                "input index 21, TR[20] unused (legacy port)"),
        "sma": "simple mean of last 20 closes, current inclusive",
        "qualifier": "close < SMA20 - 1.5*ATR20 and close > prev_close, 10:00 <= t < 15:00 ET",
        "bad_tick_rule": "H-L > 10*median(H-L), or H > max(O,C)*1.01, or L < min(O,C)*0.99",
        "caveats": caveats,
    }
    OUT.write_text(json.dumps(report, indent=2) + "\n")
    print(f"\nWrote {OUT}")


if __name__ == "__main__":
    main()
