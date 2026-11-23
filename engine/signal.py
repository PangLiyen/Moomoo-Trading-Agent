"""Indicators and the entry/exit rules from docs/STRATEGY.md section 4.

This module is the other half of the deterministic core. Like `sizing`, it
contains no judgement: given the same bars it returns the same verdict, and
nothing here reads free text or consults a model.

Two implementation notes that matter more than they look.

ATR uses Wilder's recursion, `ATR = (prev_ATR * 19 + TR) / 20`, seeded with a
simple mean of the first 20 true ranges. A plain rolling mean of TR is a
different number, and the cost gates in config were derived against this one.

The outlier filter is not optional. Two bars in nine sessions had ranges 16x
the median, and each inflates ATR20 for the following 20 bars. An inflated ATR
widens the stop, shrinks the position, AND loosens the cost gate -- three
effects all pointing toward worse trades while the risk report still looks
healthy. A bad print is therefore a data-quality event, not noise to average.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict, field
from datetime import datetime, time
from statistics import median
from typing import Any, Sequence

from .timeutil import assert_quote_fresh

ET_SESSION_OPEN = time(9, 30)
ET_SESSION_CLOSE = time(16, 0)


@dataclass(frozen=True)
class Bar:
    """One 5-minute regular-hours bar."""

    ts_et: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float

    @property
    def range(self) -> float:
        return self.high - self.low


@dataclass(frozen=True)
class Indicators:
    symbol: str
    as_of_et: datetime
    close: float
    prev_close: float
    sma20: float
    atr20: float
    bars_used: int
    excluded_outliers: tuple[str, ...] = ()

    def to_ledger(self) -> dict[str, Any]:
        d = asdict(self)
        d["as_of_et"] = self.as_of_et.isoformat()
        return d


@dataclass(frozen=True)
class SignalVerdict:
    symbol: str
    qualifies: bool
    reason_code: str
    reason_text: str
    conditions: dict[str, bool]
    detail: dict[str, Any] = field(default_factory=dict)

    def to_ledger(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Data quality
# ---------------------------------------------------------------------------

# Outlier detection. Two detectors, because there are two different faults.
OUTLIER_REFERENCE_BARS = 100
OUTLIER_MIN_WARMUP_BARS = 20
OUTLIER_WICK_TOLERANCE = 0.01     # 1% beyond the bar's own open/close


def _is_bad_print(bar: Bar, *, tolerance: float = OUTLIER_WICK_TOLERANCE) -> bool:
    """True when a bar's high or low is implausibly far from its own body.

    Scale-free and self-referential: it uses only this bar's open, high, low
    and close. No window, no median, no dependence on the volatility around
    it. That is what makes it the right detector for a bad print, which is a
    data fault rather than a market event.

    SPY 2026-11-19 15:55 printed a high of 731.89 against an open of 717.14
    and a close of 717.56 -- 2.00% above both ends of its own body, on a bar
    whose low was 716.84. Prices do not travel 15 points and return within
    five minutes while the open and close sit four cents apart. The range
    rule below happened to catch it while the median was computed globally,
    and stopped catching it when the median went trailing, because the
    trailing window was dominated by the volatile session the bad print was
    in. This detector cannot be fooled that way.
    """
    body_hi, body_lo = max(bar.open, bar.close), min(bar.open, bar.close)
    if body_hi <= 0 or body_lo <= 0:
        return False
    return bar.high > body_hi * (1.0 + tolerance) or bar.low < body_lo * (1.0 - tolerance)


def _session_open_indices(bars: Sequence[Bar]) -> set[int]:
    """Indices of each session's first bar.

    A session-open bar's range contains the overnight gap, which is real price
    information rather than an anomaly. Measuring it against intraday ranges
    compares two different things: a quiet midday median makes a perfectly
    ordinary open look like a 13x outlier. XLE 2026-11-16 09:35 was excluded
    for exactly this reason before these bars were exempted.
    """
    seen: set = set()
    opens: set[int] = set()
    for i, b in enumerate(bars):
        day = b.ts_et.date()
        if day not in seen:
            seen.add(day)
            opens.add(i)
    return opens


def flag_outlier_bars(
    bars: Sequence[Bar],
    *,
    multiple: float = 10.0,
    reference_bars: int = OUTLIER_REFERENCE_BARS,
    min_warmup: int = OUTLIER_MIN_WARMUP_BARS,
    wick_tolerance: float = OUTLIER_WICK_TOLERANCE,
) -> list[int]:
    """Indices of bars that should not feed the indicators.

    Returns indices rather than a filtered list so the caller can record
    exactly which bars were dropped. A filter that silently removes data is
    indistinguishable, afterwards, from one that removed inconvenient data.

    TWO DETECTORS, FOR TWO DIFFERENT FAULTS

    1. Bad print -- `_is_bad_print`, scale-free, applies to every bar
       including session opens and warm-up bars. A high 2% outside the body
       is a data error whatever the volatility regime.

    2. Anomalous range -- more than `multiple` x the median range of the
       preceding `reference_bars`, with session-open bars exempt.

    WHY THE RANGE MEDIAN IS TRAILING, AND FIXED-LENGTH

    The first version took the median of the whole array it was handed. A
    cross-implementation verification found what that costs: with a growing
    prefix the median moves, so a FIXED historical bar changed exclusion
    status as history accumulated. SPY 2026-11-04 10:05, range 8.686, sat on
    the boundary:

        first  63 bars   median 0.8700  ->  9.98x   kept
        first 693 bars   median 0.8650  -> 10.04x   EXCLUDED
        first 945 bars   median 1.0300  ->  8.43x   kept

    It entered and left the exclusion set 58 times across prefixes 63..693,
    and at the flip point SPY's ATR moved 4.2e-2 for no reason in the price
    data. A past bar's treatment depended on future bars, and the ATR a live
    cycle computed depended on how many bars it happened to be fed -- an
    operational parameter, not market data.

    Trailing and fixed-length fixes that, at a cost: the threshold now adapts
    to recent volatility, so range alone misses a spike inside a volatile
    stretch. Detector 1 is what covers that gap. Neither detector is
    sufficient alone, which is why the first version of this fix -- trailing
    median only -- stopped catching the one bar it was written for.

    Bars inside the warm-up are exempt from the RANGE test only, because
    there is nothing to measure them against. They are still checked for bad
    prints. Keeping a suspect warm-up bar is the safer error anyway: it
    inflates ATR, which widens stops and shrinks position size.
    """
    if not bars:
        return []

    ranges = [b.range for b in bars]
    session_opens = _session_open_indices(bars)
    flagged: set[int] = set()

    # Detector 1: bad prints. Every bar, no exemptions.
    for i, bar in enumerate(bars):
        if _is_bad_print(bar, tolerance=wick_tolerance):
            flagged.add(i)

    # Detector 2: anomalous range, trailing reference, opens exempt.
    for i in range(min_warmup, len(ranges)):
        if i in session_opens:
            continue
        window = [
            r for j, r in enumerate(ranges[max(0, i - reference_bars):i],
                                    start=max(0, i - reference_bars))
            if r > 0 and j not in session_opens
        ]
        if len(window) < min_warmup:
            continue
        med = median(window)
        if med > 0 and ranges[i] > multiple * med:
            flagged.add(i)

    return sorted(flagged)


# ---------------------------------------------------------------------------
# Indicators
# ---------------------------------------------------------------------------

def sma(values: Sequence[float], period: int) -> float:
    if len(values) < period:
        raise ValueError(f"need {period} values for SMA, got {len(values)}")
    window = values[-period:]
    return sum(window) / period


def true_range(bar: Bar, prev_close: float) -> float:
    return max(
        bar.high - bar.low,
        abs(bar.high - prev_close),
        abs(bar.low - prev_close),
    )


def atr_wilder(bars: Sequence[Bar], period: int = 20) -> float:
    """Wilder's ATR. Seeded with the simple mean of the first `period` TRs,
    then smoothed. Needs period + 1 bars because the first TR needs a prior
    close."""
    if len(bars) < period + 1:
        raise ValueError(f"need {period + 1} bars for ATR{period}, got {len(bars)}")
    trs = [true_range(bars[i], bars[i - 1].close) for i in range(1, len(bars))]
    atr = sum(trs[:period]) / period
    for tr in trs[period:]:
        atr = (atr * (period - 1) + tr) / period
    return atr


def compute_indicators(
    symbol: str,
    bars: Sequence[Bar],
    *,
    sma_period: int = 20,
    atr_period: int = 20,
    outlier_multiple: float = 10.0,
) -> Indicators:
    """Compute SMA and ATR with outliers excluded, recording which were dropped."""
    bad = set(flag_outlier_bars(bars, multiple=outlier_multiple))
    excluded = tuple(bars[i].ts_et.isoformat() for i in sorted(bad))
    clean = [b for i, b in enumerate(bars) if i not in bad]

    need = max(sma_period, atr_period + 1)
    if len(clean) < need:
        raise ValueError(
            f"{symbol}: {len(clean)} clean bars after excluding {len(bad)} "
            f"outliers; need {need}"
        )

    closes = [b.close for b in clean]
    return Indicators(
        symbol=symbol,
        as_of_et=clean[-1].ts_et,
        close=clean[-1].close,
        prev_close=clean[-2].close,
        sma20=sma(closes, sma_period),
        atr20=atr_wilder(clean, atr_period),
        bars_used=len(clean),
        excluded_outliers=excluded,
    )


# ---------------------------------------------------------------------------
# Entry rule -- docs/STRATEGY.md section 4
# ---------------------------------------------------------------------------

def evaluate_entry(
    ind: Indicators,
    *,
    regime_ok: bool,
    live_ask: float,
    quote_age_seconds: float,
    spread_pct_of_mid: float,
    now_et: datetime,
    dislocation_atr_mult: float = 1.5,
    max_quote_age_seconds: float = 120.0,
    max_spread_pct_of_mid: float = 0.05,
    entry_window: tuple[time, time] = (time(10, 0), time(15, 0)),
) -> SignalVerdict:
    """All conditions must hold. Each is reported separately so the ledger
    shows which one failed rather than only that something did.

    Freshness is an assertion, not a condition: an age outside [0, max] raises
    StaleDataError rather than returning NO_SETUP, because a book whose age
    cannot be placed (negative) or that is too old is a data fault the cycle
    must surface, not a market reading it may silently decline.

    `live_ask` is required, not optional. Section 4's rule is "price dislocated
    below SMA20 by 1.5 ATR", and a limit that crosses the book fills near the
    ask, so a bar-close test alone could permit a fill above the threshold and
    outside the rule. The ask must be positive and strictly below the same
    threshold; a missing or zero ask fails the condition rather than passing
    it. This can only refuse entries, never create one.
    """
    threshold = ind.sma20 - dislocation_atr_mult * ind.atr20
    assert_quote_fresh(quote_age_seconds, max_quote_age_seconds)

    cond = {
        "dislocated_below_sma20": ind.close < threshold,
        "ask_below_threshold": 0.0 < live_ask < threshold,
        "ticked_up": ind.close > ind.prev_close,
        "regime_ok": bool(regime_ok),
        "quote_fresh": True,
        "spread_tight": spread_pct_of_mid <= max_spread_pct_of_mid,
        "within_entry_window": entry_window[0] <= now_et.time() < entry_window[1],
    }

    detail = {
        "close": round(ind.close, 4),
        "prev_close": round(ind.prev_close, 4),
        "sma20": round(ind.sma20, 6),
        "atr20": round(ind.atr20, 6),
        "entry_threshold": round(threshold, 6),
        "distance_below_threshold": round(threshold - ind.close, 6),
        "live_ask": round(live_ask, 6),
        "ask_distance_below_threshold": round(threshold - live_ask, 6),
        "quote_age_seconds": quote_age_seconds,
        "spread_pct_of_mid": spread_pct_of_mid,
        "bars_used": ind.bars_used,
        "excluded_outliers": list(ind.excluded_outliers),
    }

    if all(cond.values()):
        return SignalVerdict(
            ind.symbol, True, "SETUP",
            f"close {ind.close:.4f} is {threshold - ind.close:.4f} below the "
            f"{threshold:.4f} threshold, ask {live_ask:.4f} also below it, "
            f"ticking up, regime clear",
            cond, detail,
        )

    failed = [k for k, v in cond.items() if not v]
    return SignalVerdict(
        ind.symbol, False, "NO_SETUP",
        "failed: " + ", ".join(failed), cond, detail,
    )


def regime_ok_from_spy(spy_bars: Sequence[Bar], *, period: int = 60) -> tuple[bool, dict[str, Any]]:
    """REGIME: SPY close above its own SMA60. Not an intraday downtrend."""
    bad = set(flag_outlier_bars(spy_bars))
    clean = [b for i, b in enumerate(spy_bars) if i not in bad]
    if len(clean) < period:
        raise ValueError(f"need {period} SPY bars for regime, got {len(clean)}")
    closes = [b.close for b in clean]
    sma60 = sma(closes, period)
    close = closes[-1]
    return close > sma60, {
        "spy_close": round(close, 4),
        "spy_sma60": round(sma60, 6),
        "regime_ok": close > sma60,
    }


# ---------------------------------------------------------------------------
# Exit rule -- whichever comes first
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Position:
    symbol: str
    qty: int
    entry_price: float
    entry_atr20: float
    entry_time_et: datetime


def evaluate_exit(
    pos: Position,
    ind: Indicators,
    *,
    now_et: datetime,
    stop_atr_mult: float = 1.0,
    max_holding_minutes: int = 60,
    session_flat_by: time = time(15, 50),
    holiday_flat_by: time | None = None,
) -> SignalVerdict:
    """Evaluate the five exits, in this order of precedence:

        1. SESSION_STOP  the flat-by deadline; never negotiable
        2. STOP_HIT      price at or below the stop level
        3. TARGET_HIT    price back at SMA20
        4. TIME_STOP     held longer than max_holding_minutes

    The order is the tie-break, and it matters: a bar that is simultaneously
    past the deadline and below the stop is labelled SESSION_STOP, because the
    deadline is the reason we are leaving regardless of price.

    An earlier version of this docstring claimed "target, stop, time stop,
    session stop", which is neither the code's order nor the intended one.
    The label is what the ledger records and what P7 would be measured from,
    so a wrong order here is a wrong exit attribution later.
    """
    stop_level = pos.entry_price - stop_atr_mult * pos.entry_atr20
    held_minutes = (now_et - pos.entry_time_et).total_seconds() / 60.0
    flat_by = holiday_flat_by or session_flat_by

    detail = {
        "entry_price": pos.entry_price,
        "close": round(ind.close, 4),
        "stop_level": round(stop_level, 6),
        "target_sma20": round(ind.sma20, 6),
        "held_minutes": round(held_minutes, 2),
        "flat_by_et": flat_by.isoformat(timespec="minutes"),
        "unrealised_per_share": round(ind.close - pos.entry_price, 4),
    }

    checks = {
        "SESSION_STOP": now_et.time() >= flat_by,
        "STOP_HIT": ind.close <= stop_level,
        "TARGET_HIT": ind.close >= ind.sma20,
        "TIME_STOP": held_minutes >= max_holding_minutes,
    }

    for code, hit in checks.items():
        if hit:
            texts = {
                "SESSION_STOP": f"{now_et.time().isoformat(timespec='minutes')} ET "
                                f"is at or past the {flat_by.isoformat(timespec='minutes')} "
                                f"flat-by deadline",
                "STOP_HIT": f"close {ind.close:.4f} at or below stop "
                            f"{stop_level:.4f}",
                "TARGET_HIT": f"close {ind.close:.4f} reached SMA20 "
                              f"{ind.sma20:.4f}",
                "TIME_STOP": f"held {held_minutes:.0f} minutes, limit "
                             f"{max_holding_minutes}",
            }
            return SignalVerdict(pos.symbol, True, code, texts[code], checks, detail)

    return SignalVerdict(
        pos.symbol, False, "HOLD",
        f"no exit condition met; {ind.close - pos.entry_price:+.4f}/share "
        f"after {held_minutes:.0f} min",
        checks, detail,
    )
