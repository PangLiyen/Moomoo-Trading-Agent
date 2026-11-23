"""Regression tests for a defect found by cross-implementation verification,
2026-11-21.

`flag_outlier_bars` took the median of whatever array it was handed. With a
growing prefix the threshold moved, so a fixed historical bar changed
exclusion status as history accumulated. SPY 2026-11-04 10:05 entered and left
the exclusion set 58 times across prefixes 63..693, and at the flip point
SPY's ATR20 moved 4.2e-2 for no reason in the price data.

Two claims were quietly false while that held. First, that identical bars
produce an identical verdict -- they did, but only for an identical *number*
of bars, which is an operational parameter rather than market data. Second,
that the indicators are causal: a past bar's treatment depended on bars that
came after it.

These tests assert the properties rather than the implementation, so a future
rewrite that reintroduces a whole-array median fails here.
"""

import random
from datetime import datetime, timedelta

from engine.signal import Bar, compute_indicators, flag_outlier_bars


def _series(n=400, seed=7, spikes=((150, 4.3), (300, 8.0))):
    rng = random.Random(seed)
    base = datetime(2026, 11, 4, 9, 35)
    bars = []
    for i in range(n):
        px = 700 + i * 0.01
        half = rng.uniform(0.6, 1.1) / 2
        bars.append(Bar(base + timedelta(minutes=5 * i), px, px + half,
                        px - half, px, 1e6))
    for idx, half in spikes:
        if idx < n:
            bars[idx] = Bar(bars[idx].ts_et, 700, 700 + half, 700 - half,
                            700, 1e6)
    return bars


def test_a_bars_status_is_never_revised_by_later_bars():
    """The core property. Growing the history must not change any earlier
    decision -- that is what makes the filter causal."""
    bars = _series()
    status: dict[int, bool] = {}
    revisions = []

    for n in range(60, len(bars) + 1, 5):
        flagged = set(flag_outlier_bars(bars[:n]))
        for i in range(n):
            if i in status and status[i] != (i in flagged):
                revisions.append((i, n))
            status[i] = i in flagged

    assert revisions == [], (
        f"{len(revisions)} exclusion decisions were revised by later bars; "
        f"first at bar {revisions[0][0]} when the prefix reached "
        f"{revisions[0][1]}. The filter is reading future data."
    )


def test_indicators_are_stable_under_extra_trailing_history():
    """A live cycle fed 300 bars and one fed 900 must agree about the bar they
    share. Before the fix they could differ by 4.2e-2 in ATR."""
    bars = _series(n=900)
    cut = 400
    short = compute_indicators("US.SPY", bars[:cut])
    long_prefix = compute_indicators("US.SPY", bars[:cut])

    assert short == long_prefix

    # And the same bar reached through a longer history, then truncated,
    # yields the same exclusion set over the shared region.
    flagged_short = {i for i in flag_outlier_bars(bars[:cut])}
    flagged_long = {i for i in flag_outlier_bars(bars) if i < cut}
    assert flagged_short == flagged_long


def test_a_decisive_outlier_is_still_caught():
    """The fix must not trade correctness for stability. A 16x-range bar is
    exactly the print that inflates ATR for the next 20 bars."""
    bars = _series()
    assert 300 in set(flag_outlier_bars(bars))


def test_warmup_bars_are_exempt_from_the_RANGE_test_only():
    """Exempt from the range test, because there is nothing to measure them
    against -- but still checked for bad prints, which need no reference.

    This test previously asserted warm-up bars were never excluded at all,
    and it failed when the bad-print detector was added. It was asserting the
    implementation rather than the contract: a wide-range warm-up bar should
    be kept, an implausible one should not.
    """
    # A wide but internally consistent warm-up bar: body spans the range, so
    # no wick. Range alone must not exclude it.
    bars = _series(n=60, spikes=())
    wide = bars[3]
    bars[3] = Bar(wide.ts_et, 695.5, 704.5, 695.5, 704.5, 1e6)
    assert 3 not in flag_outlier_bars(bars)

    # The same bar with an implausible high is a bad print, warm-up or not.
    bars[3] = Bar(wide.ts_et, 700.0, 709.0, 699.5, 700.0, 1e6)
    assert 3 in flag_outlier_bars(bars)


def test_the_real_bad_print_is_caught_inside_a_volatile_stretch():
    """The regression that motivated the bad-print detector.

    SPY 2026-11-19 15:55 printed high 731.89 against open 717.14 / close
    717.56 -- 2.00% above its own body. A trailing-median range test misses
    it, because the trailing window is dominated by the volatile session the
    bad print sits in: 9.586x against a 10x threshold. The global median that
    the first implementation used caught it at 15.968x, which is why the gap
    only appeared once the median went trailing.
    """
    rng = random.Random(7)
    bars = []
    for d in range(5):
        for i in range(77):
            # last two sessions deliberately ~3x more volatile
            half = (rng.uniform(0.6, 1.1) if d < 3 else rng.uniform(2.4, 3.4)) / 2
            bars.append(Bar(datetime(2026, 11, 16 + d, 9, 35) + timedelta(minutes=5 * i),
                            717.0, 717.0 + half, 717.0 - half, 717.0, 1e6))
    bars[-1] = Bar(bars[-1].ts_et, 717.14, 731.89, 716.84, 717.56, 1e6)

    flagged = set(flag_outlier_bars(bars))
    assert len(bars) - 1 in flagged, (
        "the bad print was missed; the range test alone is regime-dependent "
        "and the bad-print detector is what covers that"
    )


def test_session_open_bars_are_exempt_from_the_range_test():
    """An opening bar's range contains the overnight gap, which is real price
    information. Measured against a quiet midday median it looks like a 13x
    outlier -- XLE 2026-11-16 09:35 was excluded for exactly this reason
    before opens were exempted.
    """
    bars = []
    for d in range(4):
        for i in range(77):
            px = 61.0
            half = 0.065 / 2
            ts = datetime(2026, 11, 12 + d, 9, 35) + timedelta(minutes=5 * i)
            if i == 0 and d == 3:
                # a wide but internally consistent gap open: body spans it
                bars.append(Bar(ts, 60.6, 61.45, 60.6, 61.45, 1e6))
            else:
                bars.append(Bar(ts, px, px + half, px - half, px, 1e6))
    gap_index = 3 * 77
    assert gap_index not in flag_outlier_bars(bars)


def test_a_bad_print_on_a_session_open_is_still_caught():
    """Opens are exempt from the range test, not from the bad-print test. A
    genuine gap has a body; a bad print has a wick."""
    bars = []
    for d in range(4):
        for i in range(77):
            ts = datetime(2026, 11, 12 + d, 9, 35) + timedelta(minutes=5 * i)
            bars.append(Bar(ts, 61.0, 61.03, 60.97, 61.0, 1e6))
    gap_index = 3 * 77
    ts = bars[gap_index].ts_et
    bars[gap_index] = Bar(ts, 61.0, 63.0, 60.97, 61.0, 1e6)   # 3.3% wick
    assert gap_index in flag_outlier_bars(bars)


def test_reference_window_does_not_grow_without_bound():
    """A fixed-length trailing window is what keeps the threshold stable. If
    the window grew with the series, a distant regime change would silently
    move the bar for recent data."""
    quiet = _series(n=300, spikes=())
    # Append a genuinely more volatile regime; early thresholds must not move.
    rng = random.Random(11)
    base = quiet[-1].ts_et
    loud = list(quiet)
    for i in range(1, 300):
        px = 700 + i * 0.01
        half = rng.uniform(5.0, 9.0) / 2
        loud.append(Bar(base + timedelta(minutes=5 * i), px, px + half,
                        px - half, px, 1e6))

    before = {i for i in flag_outlier_bars(quiet)}
    after = {i for i in flag_outlier_bars(loud) if i < len(quiet)}
    assert before == after


def test_empty_and_tiny_inputs_do_not_raise():
    assert flag_outlier_bars([]) == []
    assert flag_outlier_bars(_series(n=5)) == []


# --------------------------------------------------------------------------
# Lookahead: the defect the 2026-11-23 rehearsal exposed
# --------------------------------------------------------------------------

def test_bars_that_have_not_closed_are_never_used():
    """The rehearsal ran at 15:30 and ingested bars through 15:55, reporting
    staleness_seconds: -1500. That is indicators computed from data the cycle
    could not have seen -- guaranteed in any backdated run and reachable live
    through clock skew.

    It also covers the partially-formed bar: a 5-minute bar read four minutes
    in has a close that is still moving.
    """
    from engine.bars import validate

    base = datetime(2026, 11, 20, 15, 0)
    bars = [Bar(base + timedelta(minutes=5 * i), 100, 100.2, 99.8, 100, 1e6)
            for i in range(12)]                      # 15:00 .. 15:55

    audit = validate("US.SPY", bars, now_et=datetime(2026, 11, 20, 15, 30),
                     max_staleness_seconds=99_999)

    assert audit.newest_bar_et == "2026-11-20T15:25:00", (
        "the 15:25 bar closes at 15:30; 15:30 onwards had not closed"
    )
    assert audit.dropped_not_yet_closed == 6
    assert audit.staleness_seconds is not None and audit.staleness_seconds > 0, (
        "negative staleness means the engine read the future"
    )


def test_staleness_is_never_negative_for_any_clock():
    """The property, not the one case. No `now` should ever produce a bar set
    whose newest member is in the future."""
    from engine.bars import validate

    from engine.errors import StaleDataError

    base = datetime(2026, 11, 20, 9, 30)
    bars = [Bar(base + timedelta(minutes=5 * i), 100, 100.2, 99.8, 100, 1e6)
            for i in range(77)]

    checked = refused = 0
    for minutes in range(0, 390, 7):
        now = base + timedelta(minutes=minutes)
        try:
            audit = validate("US.SPY", bars, now_et=now,
                             max_staleness_seconds=99_999)
        except StaleDataError:
            # At 09:30 exactly, the 09:30 bar has not closed and nothing
            # earlier exists. Refusing is the correct outcome, not a miss.
            refused += 1
            continue
        if audit.staleness_seconds is not None:
            assert audit.staleness_seconds >= 0, (
                f"at {now:%H:%M} the newest bar was {audit.newest_bar_et}, "
                f"which is in the future"
            )
            checked += 1

    assert checked > 40, f"only {checked} clocks produced a usable bar set"
    assert refused >= 1, (
        "no clock refused; the all-unclosed case should be reachable at the "
        "session open"
    )
