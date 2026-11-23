"""The cycle report table, ordered by distance to trigger.

At 10:27 ET on 2026-11-23, XLE was 0.58 ATR from firing and SPY was 6.74, but
the written report named SPY as the closest symbol -- the table was printed in
universe order and "closest" was read off row one. Sorting by the distance
column removes that error class: whichever row is first is the closest.

Distance is (close - threshold) / atr20, in ATR units. Zero is at the
trigger; negative values are dislocated past it.
"""

from __future__ import annotations

from typing import Any, Iterable


def distance_to_trigger_atr(close: float, threshold: float, atr20: float) -> float:
    if atr20 <= 0:
        raise ValueError(f"ATR must be positive to scale a distance, got {atr20}")
    return (close - threshold) / atr20


def sort_by_distance(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Ascending by distance_atr: the nearest-to-firing symbol prints first."""
    return sorted(rows, key=lambda r: r["distance_atr"])
