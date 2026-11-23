"""The report table is ordered by distance to trigger.

At 10:27 ET on 2026-11-23 the table printed in universe order and the reading
named SPY as the closest symbol. XLE was 0.58 ATR from firing; SPY was 6.74.
Sorting the column removes the error: the first row is the closest.
"""

from engine.report import distance_to_trigger_atr, sort_by_distance

# 2026-11-23 10:27 ET cycle: (close, sma20, atr20) straight from the report.
CYCLE_3 = {
    "US.SPY": (724.2200, 706.1624, 3.4471),
    "US.QQQ": (653.5900, 630.2780, 4.3126),
    "US.IWM": (264.9800, 260.8925, 1.0859),
    "US.XLF": (55.6500, 55.1690, 0.1731),
    "US.XLE": (60.5800, 60.7531, 0.1885),
}


def _rows():
    rows = []
    for sym, (close, sma20, atr20) in CYCLE_3.items():
        threshold = sma20 - 1.5 * atr20
        rows.append({"symbol": sym,
                     "distance_atr": distance_to_trigger_atr(
                         close, threshold, atr20)})
    return rows


def test_the_cycle_three_table_orders_xle_first():
    ordered = sort_by_distance(_rows())
    assert ordered[0]["symbol"] == "US.XLE"
    assert ordered[-1]["symbol"] == "US.QQQ"
    assert ordered[0]["distance_atr"] < ordered[1]["distance_atr"]


def test_distance_matches_the_measured_values():
    by_sym = {r["symbol"]: r["distance_atr"] for r in _rows()}
    assert round(by_sym["US.XLE"], 2) == 0.58
    assert round(by_sym["US.SPY"], 2) == 6.74


def test_zero_atr_is_refused():
    import pytest

    with pytest.raises(ValueError):
        distance_to_trigger_atr(100.0, 99.0, 0.0)
