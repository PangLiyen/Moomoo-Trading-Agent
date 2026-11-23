"""Bar validation: regular hours only, correct interval, no stale files.

WHY THIS IS A MODULE AND NOT A COMMENT

`STRATEGY.md` section 4 states that calculations use 5-minute regular-hours
bars and that "pre-market and after-hours bars are excluded from all
calculations." Nothing in the live path enforced either clause. The analysis
scripts filtered; the driver accepted any bars at any interval, and
`ET_SESSION_OPEN` / `ET_SESSION_CLOSE` were defined in signal.py and never
read.

A pre-market bar entering the indicators is not a visible failure. It shifts
SMA20 and ATR20 silently, which moves the entry threshold and the position
size, and the ledger records the result as a normal decision. That is the
worst shape a defect can take here: wrong numbers, confidently logged.

So the rules are enforced at the boundary, every bar is accounted for, and
what was dropped is recorded rather than discarded quietly.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Any, Iterable, Sequence

from .errors import StaleDataError
from .signal import Bar

# US equity regular trading hours, Eastern. A 5-minute bar is labelled by its
# START, so the last regular bar of a normal session starts at 15:55 and the
# 16:00 print is a closing auction artefact rather than a tradeable bar.
RTH_FIRST_BAR_START = time(9, 30)
RTH_LAST_BAR_START = time(15, 55)
EXPECTED_INTERVAL = timedelta(minutes=5)


@dataclass(frozen=True)
class BarAudit:
    """What survived validation, and everything that did not."""

    symbol: str
    kept: tuple[Bar, ...]
    dropped_outside_rth: int
    dropped_duplicate_timestamps: int
    dropped_zero_or_negative: int
    dropped_not_yet_closed: int
    interval_violations: tuple[str, ...]
    sessions: tuple[str, ...]
    newest_bar_et: str | None
    staleness_seconds: float | None

    def to_ledger(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "bars_kept": len(self.kept),
            "dropped_outside_rth": self.dropped_outside_rth,
            "dropped_duplicate_timestamps": self.dropped_duplicate_timestamps,
            "dropped_zero_or_negative": self.dropped_zero_or_negative,
            "dropped_not_yet_closed": self.dropped_not_yet_closed,
            "interval_violations": list(self.interval_violations),
            "sessions": list(self.sessions),
            "newest_bar_et": self.newest_bar_et,
            "staleness_seconds": self.staleness_seconds,
        }


def in_rth(ts: datetime) -> bool:
    """A bar start inside regular hours, Monday to Friday.

    The 16:00 closing print is excluded: it is labelled at the session close,
    carries auction volume, and is not a five-minute trading interval. Every
    committed analysis script drops it, so the live path must too or the two
    disagree about what a session contains.
    """
    if ts.weekday() > 4:
        return False
    return RTH_FIRST_BAR_START <= ts.time() <= RTH_LAST_BAR_START


def validate(
    symbol: str,
    bars: Sequence[Bar],
    *,
    now_et: datetime | None = None,
    max_staleness_seconds: float | None = None,
    expected_interval: timedelta = EXPECTED_INTERVAL,
) -> BarAudit:
    """Filter to valid RTH bars and audit what was removed.

    Raises StaleDataError when the newest surviving bar is older than
    `max_staleness_seconds`. Refusing is deliberate: a cycle that computes
    indicators from yesterday's bars and submits an order against today's
    price is the stale-file hazard, and it produces a plausible-looking order
    rather than an error.
    """
    ordered = sorted(bars, key=lambda b: b.ts_et)

    outside = sum(1 for b in ordered if not in_rth(b.ts_et))
    rth = [b for b in ordered if in_rth(b.ts_et)]

    bad_values = [
        b for b in rth
        if b.high < b.low or min(b.open, b.high, b.low, b.close) <= 0
    ]
    rth = [b for b in rth if b not in bad_values]

    seen: set[datetime] = set()
    deduped: list[Bar] = []
    duplicates = 0
    for b in rth:
        if b.ts_et in seen:
            duplicates += 1
            continue
        seen.add(b.ts_et)
        deduped.append(b)

    # Interval check, within a session only. A gap across the overnight close
    # is expected; a gap inside one is a missing bar, and a sub-interval step
    # means the feed is not the resolution we think it is.
    def _interval_violations(seq):
        out = []
        for prev, cur in zip(seq, seq[1:]):
            if prev.ts_et.date() != cur.ts_et.date():
                continue
            delta = cur.ts_et - prev.ts_et
            if delta != expected_interval:
                out.append(f"{prev.ts_et:%Y-%m-%d %H:%M} -> {cur.ts_et:%H:%M} "
                           f"is {delta}, expected {expected_interval}")
        return out

    violations: list[str] = []
    for prev, cur in zip(deduped, deduped[1:]):
        if prev.ts_et.date() != cur.ts_et.date():
            continue
        delta = cur.ts_et - prev.ts_et
        if delta != expected_interval:
            violations.append(
                f"{prev.ts_et:%Y-%m-%d %H:%M} -> {cur.ts_et:%H:%M} "
                f"is {delta}, expected {expected_interval}"
            )

    violations = _interval_violations(deduped)
    sessions = sorted({b.ts_et.date().isoformat() for b in deduped})
    # A bar is usable only once it has CLOSED. Two distinct faults are
    # excluded here, and the second is the one that bit:
    #
    #   - a bar starting after `now_et` cannot have happened yet
    #   - a bar that has started but not finished has a close that is still
    #     moving, so reading it is reading a number that will change
    #
    # The first version only rejected POSITIVE staleness, so a rehearsal at
    # 15:30 happily ingested bars through 15:55 and reported
    # staleness_seconds: -1500. That is lookahead: indicators computed from
    # data the cycle could not have seen. It is guaranteed in any backdated
    # run and reachable live through clock skew, and it is the one category
    # of defect that invalidates a result rather than degrading it.
    unclosed = 0
    if now_et is not None:
        closed = [b for b in deduped if b.ts_et + expected_interval <= now_et]
        unclosed = len(deduped) - len(closed)
        deduped = closed

    newest = deduped[-1].ts_et if deduped else None

    staleness = None
    if newest is not None and now_et is not None:
        staleness = (now_et - newest).total_seconds()
        if max_staleness_seconds is not None and staleness > max_staleness_seconds:
            raise StaleDataError(
                f"newest closed bar is {newest:%Y-%m-%d %H:%M} ET, "
                f"{staleness / 60:.1f} minutes old, limit "
                f"{max_staleness_seconds / 60:.1f}. A cycle computing "
                f"indicators from stale bars submits a plausible order at the "
                f"wrong price, so this refuses rather than proceeding."
            )
    elif now_et is not None and unclosed:
        raise StaleDataError(
            f"every bar supplied starts at or after {now_et:%Y-%m-%d %H:%M} "
            f"ET, so none has closed. Computing indicators from them would "
            f"be reading data the cycle cannot have seen."
        )

    return BarAudit(
        symbol=symbol,
        kept=tuple(deduped),
        dropped_outside_rth=outside,
        dropped_duplicate_timestamps=duplicates,
        dropped_zero_or_negative=len(bad_values),
        dropped_not_yet_closed=unclosed,
        interval_violations=tuple(violations[:20]),
        sessions=tuple(sessions),
        newest_bar_et=newest.isoformat() if newest else None,
        staleness_seconds=staleness,
    )


def session_bar_counts(bars: Iterable[Bar]) -> dict[str, int]:
    """Bars per session, for asserting a feed returned what was asked for.

    A missing trading day comes back as absence rather than an error, so the
    count has to be checked rather than assumed — measured during the
    Session 00 probe when `quote_history_kline` silently omitted 11-19 for
    some symbols.
    """
    return dict(Counter(b.ts_et.date().isoformat() for b in bars))
