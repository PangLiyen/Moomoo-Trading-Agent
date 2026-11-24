"""Session coverage: the audit that records a session the system missed.

N38. Every guard in the engine is conditional on a cycle running, so the
system cannot detect its own absence. `session_coverage` is what makes the
absence legible after the fact; these tests pin its arithmetic -- a hole is
named, a clean session is not smeared with false positives -- because an
audit that over-reports is as useless as one that stays silent.

The 2026-11-23 instance: 13 cycles, window 09:41..15:50, 29 expected slots,
and a 2h51m tail where the close cycle never ran.
"""

from datetime import date, datetime, timedelta

from engine.coverage import COVERED, PARTIAL, compute_coverage


def _cycle(when: datetime) -> dict:
    return {"stage": "cycle", "cycle_id": f"C{when:%Y%m%dT%H%M}",
            "verdict": "NO_ACTION"}


D = date(2026, 11, 23)


def test_a_ledger_with_a_known_hole_lists_exactly_those_gaps():
    records = [
        # Records from other stages sharing the cycle id must not be counted;
        # a `cycle` record is the driver's own statement that it finished.
        {"stage": "session_plan", "cycle_id": "C20261123T0941"},
        _cycle(datetime(2026, 11, 23, 9, 41)),
        _cycle(datetime(2026, 11, 23, 9, 53)),      # 720s: inside cadence
        _cycle(datetime(2026, 11, 23, 10, 19)),     # 1560s: 26 minutes
        _cycle(datetime(2026, 11, 23, 10, 32)),     # 780s: exactly cadence
        {"stage": "cadence", "cycle_id": "C20261123T1019"},   # not a cycle
    ]

    cov = compute_coverage(
        records, trading_date=D, cadence_seconds=780,
        window_end_et=datetime(2026, 11, 23, 11, 0),
    )

    assert [(g["from_et"], g["to_et"], g["seconds"]) for g in cov.gaps] == [
        ("2026-11-23T09:53", "2026-11-23T10:19", 1560.0),
        # the tail: 10:32 to the 11:00 end, where the close slot never ran
        ("2026-11-23T10:32", "2026-11-23T11:00", 1680.0),
    ]
    assert cov.gaps[1]["next_cycle_id"] is None
    assert cov.longest_gap_seconds == 1680.0
    assert cov.actual_cycles == 4
    assert cov.expected_slots == 7        # (11:00-09:41)=4740s // 780 + 1
    assert cov.missing_slots == 3
    assert cov.verdict == PARTIAL


def test_a_complete_session_has_no_gaps():
    start = datetime(2026, 11, 23, 9, 30)
    end = datetime(2026, 11, 23, 15, 50)
    slots = int((end - start).total_seconds() // 780) + 1
    records = [_cycle(start + timedelta(seconds=780 * k)) for k in range(slots)]

    cov = compute_coverage(
        records, trading_date=D, cadence_seconds=780, window_end_et=end)

    assert cov.gaps == ()
    assert cov.expected_slots == slots == 30
    assert cov.actual_cycles == 30
    assert cov.missing_slots == 0
    assert cov.longest_gap_seconds == 0.0
    assert cov.verdict == COVERED


def test_another_dates_cycles_are_not_counted():
    """The record is per session. Yesterday's tail is not today's coverage."""
    records = [
        _cycle(datetime(2026, 11, 20, 15, 55)),
        _cycle(datetime(2026, 11, 23, 9, 41)),
        _cycle(datetime(2026, 11, 23, 10, 0)),
    ]
    cov = compute_coverage(
        records, trading_date=D, cadence_seconds=780,
        window_end_et=datetime(2026, 11, 23, 10, 10),
    )
    assert cov.actual_cycles == 2
    assert cov.window_start_et == datetime(2026, 11, 23, 9, 41)
    assert [g["seconds"] for g in cov.gaps] == [1140.0]


def test_no_cycles_is_refused_rather_than_zeroed():
    """A zeroed record would read as covered, which is the opposite of true."""
    try:
        compute_coverage([], trading_date=D, cadence_seconds=780,
                         window_end_et=datetime(2026, 11, 23, 15, 50))
    except ValueError as exc:
        assert "no completed cycles" in str(exc)
    else:
        raise AssertionError("an empty ledger must raise, not report coverage")
