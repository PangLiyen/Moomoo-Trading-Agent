"""Session coverage, read from the ledger.

EVERY GUARD IS CONDITIONAL ON A CYCLE RUNNING

The kill switch, the software stops, the staleness gates and the account guard
all execute inside a cycle. None of them executes if no cycle runs. On
2026-11-23 the cadence stopped at 12:59 ET -- the driver was never invoked
again that session -- and nothing in the system noticed, because the thing
that would notice is a cycle and there was none. The engine was flat, so no
position went unwatched; the record simply has a hole and no record says so.

This module is the audit, not the guarantee. It reads completed cycles from
the ledger and states what is missing. It cannot make a cycle happen: a
process that is not running cannot report that it is not running. Liveness
requires a scheduler outside this process, which is the Cloud Agents /
Forward Mode gap tracked in `docs/SCORING_PLAN.md` section 2.

WHY THE WINDOW STARTS AT THE FIRST CYCLE, NOT AT THE OPEN

The engine cannot see when a session was meant to start -- the runbook says
"first cycle at the open", but intent leaves no trace. The first completed
cycle is the evidence the session began (09:41 on 11-23). The window ends at
the plan's flat-by (15:50), which is the last slot the runbook schedules, and
the interval from the last cycle to that end is itself a gap when it exceeds
the cadence: the close cycle is exactly the one most damaging to miss.

A session that starts late therefore has a smaller expected-slot count rather
than a large deficit at the front. The record states `window_start_et`
alongside the count so a reader can see which it was.

WHAT COUNTS AS A GAP

Any interval between consecutive completed cycles longer than the cadence,
plus the interval from the last cycle to the window end. Each gap carries its
start, its end and its length in seconds; a per-gap slot count is not stated
because cycles do not in fact land on a grid, and dividing a drifted interval
into slots would invent precision the timestamps do not support.
`missing_slots` is the honest summary: expected minus actual.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Mapping, Sequence

COVERED = "COVERED"
PARTIAL = "PARTIAL"

LIVE = "live"
RECONSTRUCTED = "reconstructed"


@dataclass(frozen=True)
class SessionCoverage:
    trading_date: date
    basis: str                      # LIVE or RECONSTRUCTED
    cadence_seconds: float
    window_start_et: datetime       # first completed cycle
    window_end_et: datetime         # the plan's flat-by
    expected_slots: int
    actual_cycles: int
    cycle_ids: tuple[str, ...]
    gaps: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    longest_gap_seconds: float = 0.0

    @property
    def missing_slots(self) -> int:
        return max(0, self.expected_slots - self.actual_cycles)

    @property
    def verdict(self) -> str:
        return COVERED if not self.gaps and self.missing_slots == 0 else PARTIAL

    def to_ledger(self) -> dict[str, Any]:
        return {
            "trading_date": self.trading_date.isoformat(),
            "basis": self.basis,
            "cadence_seconds": self.cadence_seconds,
            "window_start_et": self.window_start_et.isoformat(timespec="minutes"),
            "window_end_et": self.window_end_et.isoformat(timespec="minutes"),
            "expected_slots": self.expected_slots,
            "actual_cycles": self.actual_cycles,
            "missing_slots": self.missing_slots,
            "longest_gap_seconds": round(self.longest_gap_seconds, 3),
            "gaps": list(self.gaps),
            "cycle_ids": list(self.cycle_ids),
        }


def completed_cycles(
    records: Sequence[Mapping[str, Any]], trading_date: date
) -> list[tuple[str, datetime]]:
    """Distinct completed cycles for a date, oldest first.

    A `cycle` record is the driver's own statement that a cycle finished, so
    it is the only stage counted. `cycle_id` carries ET wall-clock to the
    minute, which is the resolution the cadence is stated in.
    """
    prefix = f"C{trading_date:%Y%m%d}"
    seen: dict[str, datetime] = {}
    for rec in records:
        if rec.get("stage") != "cycle":
            continue
        cid = str(rec.get("cycle_id") or "")
        if not cid.startswith(prefix) or cid in seen:
            continue
        try:
            seen[cid] = datetime.strptime(cid[1:], "%Y%m%dT%H%M")
        except ValueError:
            continue
    return sorted(seen.items(), key=lambda kv: kv[1])


def compute_coverage(
    records: Sequence[Mapping[str, Any]],
    *,
    trading_date: date,
    cadence_seconds: float,
    window_end_et: datetime,
    basis: str = LIVE,
) -> SessionCoverage:
    """Coverage for one trading date. Raises when there is nothing to anchor.

    No completed cycles means there is no session to audit and no evidence
    the driver ever ran; returning zeros would read as 'covered'.
    """
    cycles = completed_cycles(records, trading_date)
    if not cycles:
        raise ValueError(
            f"no completed cycles for {trading_date}: there is no session to "
            f"measure, and a zeroed record would read as covered"
        )
    if cadence_seconds <= 0:
        raise ValueError(f"cadence_seconds must be positive, got {cadence_seconds!r}")

    start = cycles[0][1]
    end = window_end_et
    expected = int((end - start).total_seconds() // cadence_seconds) + 1

    gaps: list[dict[str, Any]] = []
    longest = 0.0
    for (_, prev), (cid, cur) in zip(cycles, cycles[1:]):
        delta = (cur - prev).total_seconds()
        if delta > cadence_seconds:
            gaps.append({
                "from_et": prev.isoformat(timespec="minutes"),
                "to_et": cur.isoformat(timespec="minutes"),
                "seconds": round(delta, 3),
                "next_cycle_id": cid,
            })
            longest = max(longest, delta)
    tail = (end - cycles[-1][1]).total_seconds()
    if tail > cadence_seconds:
        gaps.append({
            "from_et": cycles[-1][1].isoformat(timespec="minutes"),
            "to_et": end.isoformat(timespec="minutes"),
            "seconds": round(tail, 3),
            "next_cycle_id": None,          # the close slot never ran
        })
        longest = max(longest, tail)

    return SessionCoverage(
        trading_date=trading_date,
        basis=basis,
        cadence_seconds=cadence_seconds,
        window_start_et=start,
        window_end_et=end,
        expected_slots=expected,
        actual_cycles=len(cycles),
        cycle_ids=tuple(cid for cid, _ in cycles),
        gaps=tuple(gaps),
        longest_gap_seconds=longest,
    )
