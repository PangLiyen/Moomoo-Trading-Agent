"""One epoch -> ET conversion, and the freshness assertions.

Three timezone defects have been found in this project: `utcfromtimestamp`
applied to a bar `time_key` that was already ET-labelled, `staleness_seconds`
of -1500 from unclosed bars, and a quote age computed with the offset applied
twice (ages near -5 hours). Each was fixed at its own call site. This module
exists so there is one place left to get wrong, and one place to test.

Two inputs now carry a freshness bound: the order book (`assert_quote_fresh`)
and the order-list snapshot the foreign-order guard attests to
(`assert_order_list_fresh`). Both refuse an age outside [0, max] rather than
passing it.

ET is fixed at UTC-5. The competition window is entirely in EST, and a
DST-aware zone would shift historical bars relative to the ET session times in
config -- correct for the calendar, wrong for reconciling against them.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .errors import StaleDataError

ET_OFFSET = timedelta(hours=-5)
ET = timezone(ET_OFFSET)


def epoch_ms_to_et(value: int | float) -> datetime:
    """Epoch milliseconds (or seconds) -> naive ET wall-clock.

    MoMoo's bar `time_key` and order-book `exchange_data_time_ms` are both
    absolute epoch values, so both convert through here. Callers that render
    or compare ET times must use this rather than doing their own arithmetic.
    """
    number = float(value)
    secs = number / 1000.0 if number > 1e11 else number
    return datetime.fromtimestamp(secs, tz=ET).replace(tzinfo=None)


def quote_age_seconds(book_ts_ms: int | float, now_et: datetime) -> float:
    """Age of an order book, from its own exchange timestamp."""
    return (now_et - epoch_ms_to_et(book_ts_ms)).total_seconds()


def assert_quote_fresh(age_seconds: float, max_seconds: float) -> float:
    """0 <= age <= max, or raise StaleDataError.

    The lower bound is the point of this function. A negative age means the
    timestamp could not be placed on the clock -- usually a conversion defect;
    measured once at near -5 hours -- and a one-sided `age < max` check waves
    it through as fresh. No feed produces a book from the future, so a negative
    age is never a fast market, it is our arithmetic failing.
    """
    if not (age_seconds >= 0):
        raise StaleDataError(
            f"quote age {age_seconds!r}s is negative: the timestamp cannot be "
            f"placed on the clock, so freshness is unproven and the book must "
            f"not be used to price an order"
        )
    if age_seconds > max_seconds:
        raise StaleDataError(
            f"quote age {age_seconds:.1f}s exceeds {max_seconds:.0f}s: the "
            f"book is too old to price an order against"
        )
    return age_seconds


def assert_order_list_fresh(age_seconds: float, max_seconds: float) -> float:
    """0 <= age <= max for an order-list snapshot, or raise StaleDataError.

    The foreign-order guard asks whether the account contains any order our
    ledger cannot account for. It answers that question from a snapshot the
    operator captured, so its answer is only as current as the capture. On
    2026-11-23 cycle 5 decided on a 10:47:56 capture while a foreign order
    was created at 10:52:29: the guard passed because it was shown a state
    that predated the event it exists to detect.

    A snapshot whose capture time cannot be placed (negative age) or that is
    older than the bound is refused rather than attested to. Refusing is the
    only honest answer to "did anything change that I could not see".
    """
    if not (age_seconds >= 0):
        raise StaleDataError(
            f"order list age {age_seconds!r}s is negative: the capture time "
            f"cannot be placed on the clock, so the guard has not seen the "
            f"account's order state and must not attest to it"
        )
    if age_seconds > max_seconds:
        raise StaleDataError(
            f"order list age {age_seconds:.1f}s exceeds {max_seconds:.0f}s: "
            f"the snapshot is too old to attest to the account's order state, "
            f"so the foreign-order guard refuses to pass on a state it cannot "
            f"see"
        )
    return age_seconds
