"""N34: the two bar-selection paths, compared over the same window.

The engine path (`run_cycle.load_bars` -> `engine.bars.validate`) and the
analysis path (`analysis/compute_indicators.py` session filter) were written
independently. Nothing ran both over the same input until this test. They
agreed on 2026-11-18..19 by coincidence: the analysis scripts had always read
the label as a close (a bar labelled T is finished at T), and the engine did
not until 24bf0e7 (N33). The agreement was therefore between a correct path
and a path about to be corrected, and no test would have noticed the engine
falling back.

Two stages, compared separately because the implementations split them
differently: the engine admits a bar in `validate` and quarantines bad prints
later, inside `compute_indicators`; the analysis path drops bad prints before
building its series. Comparing "admitted" against "cleaned" reports the SPY
2026-11-19 15:55 bad print as a selection difference, which it is not. So:

  1. admission -- which bars each path admits at all (16:00 print, pre-market,
     RTH window, interval)
  2. signals   -- end to end, each path's own indicators and own exclusions

This does not merge the two implementations -- that would delete the
comparison. `analysis/verify_implementations.py` stays separate and
arithmetically independent: it compares indicator values against a third
implementation. Here the question is only whether the two production paths
produce the same answers.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "analysis"))

import compute_indicators as ci  # noqa: E402
import measure_signal_rate as msr  # noqa: E402
import run_cycle  # noqa: E402
from engine import validate_bars  # noqa: E402
from engine.signal import (  # noqa: E402
    compute_indicators, evaluate_entry, regime_ok_from_spy,
)

ARCHIVE = ROOT / "analysis" / "archive"
WINDOW = ("2026-11-18", "2026-11-19")
SYMBOLS = ci.SYMBOLS


def _raw_rows(sym: str) -> list[dict]:
    return json.loads((ARCHIVE / f"bars_US_{sym}_full.json").read_text())


def _engine_admitted(tmp_path: Path, sym: str):
    """The driver's own path: file -> load_bars -> validate_bars."""
    path = tmp_path / f"bars_US_{sym}.json"
    path.write_text(json.dumps(_raw_rows(sym)))
    bars = run_cycle.load_bars(path)
    return list(validate_bars(sym, bars, now_et=bars[-1].ts_et).kept)


def _analysis_admitted(sym: str) -> list[dict]:
    return ci.session_bars(ci.load_bars(sym))


def _analysis_clean(sym: str) -> list[dict]:
    """The analysis path's series: admitted bars minus its bad-tick rule."""
    raw = _analysis_admitted(sym)
    window = [b for b in raw if msr.BASELINE_FROM <= b["date"] <= msr.BASELINE_TO]
    _, flagged = ci.find_bad_ticks(window)
    excluded = {f["tk"] for f in flagged}
    return [b for b in raw if b["tk"] not in excluded]


def _in_window(date: str) -> bool:
    return WINDOW[0] <= date <= WINDOW[1]


@pytest.fixture(scope="module")
def paths(tmp_path_factory):
    """Each path's admitted bars plus the per-bar state the signals need.

    Engine indicator snapshots are taken only at bars inside the signal window:
    each call re-runs the engine on a prefix, so the full history is the
    expensive part, not the number of window bars.
    """
    tmp = tmp_path_factory.mktemp("equiv")
    data = {}
    for sym in SYMBOLS:
        eng_bars = _engine_admitted(tmp, sym)
        ana_admitted = _analysis_admitted(sym)
        ana_series = ci.compute_series([dict(b) for b in _analysis_clean(sym)])
        eng_ind = {}
        for i, b in enumerate(eng_bars):
            if not _in_window(b.ts_et.strftime("%Y-%m-%d")):
                continue
            try:
                eng_ind[b.ts_et] = compute_indicators(sym, eng_bars[: i + 1])
            except ValueError:
                pass
        data[sym] = {
            "engine_admitted": eng_bars,
            "analysis_admitted": ana_admitted,
            "analysis_series": ana_series,
            "engine_ind": eng_ind,
        }
    return data


def test_the_two_paths_admit_the_same_bars(paths):
    for sym in SYMBOLS:
        eng = [b.ts_et.strftime("%Y-%m-%d %H:%M")
               for b in paths[sym]["engine_admitted"]]
        ana = [f"{b['date']} {b['hhmm']}"
               for b in paths[sym]["analysis_admitted"]]
        # Compared over the whole file, not only the signal window: a bar
        # admitted by one path and not the other shifts every indicator after
        # it, and that is the failure this test exists to catch.
        assert eng == ana, (
            f"{sym}: the engine and the analysis path do not admit the same "
            f"bars. engine-only: {sorted(set(eng) - set(ana))[:5]}, "
            f"analysis-only: {sorted(set(ana) - set(eng))[:5]}"
        )


def test_the_two_paths_derive_the_same_signals(paths):
    """Same rule, each path's own indicators, exclusions and regime series."""
    ana_spy = paths["SPY"]["analysis_series"]
    regime_by_tk = {b["tk"]: b["regime_ok"]
                    for b in msr.regime_series(ana_spy)}

    eng_spy = paths["SPY"]["engine_admitted"]
    regime_by_time = {}
    for i, b in enumerate(eng_spy):
        if not _in_window(b.ts_et.strftime("%Y-%m-%d")):
            continue
        try:
            regime_by_time[b.ts_et] = regime_ok_from_spy(eng_spy[: i + 1])[0]
        except ValueError:
            continue

    for sym in SYMBOLS:
        eng_signals = {
            ind.as_of_et.strftime("%Y-%m-%d %H:%M")
            for ind in paths[sym]["engine_ind"].values()
            if evaluate_entry(ind, regime_ok=True, live_ask=ind.close,
                              quote_age_seconds=0.0, spread_pct_of_mid=0.0,
                              now_et=ind.as_of_et).qualifies
            and regime_by_time.get(ind.as_of_et)
        }

        ana_signals = {
            f"{b['date']} {b['hhmm']}"
            for b in paths[sym]["analysis_series"]
            if _in_window(b["date"])
            and b["sma20"] is not None and b["atr20"] is not None
            and b["prev_close"] is not None
            and ci.ENTRY_WINDOW_START_MIN <= b["mins"] < ci.ENTRY_WINDOW_END_MIN
            and b["c"] < b["sma20"] - ci.DISLOCATION_ATR_MULT * b["atr20"]
            and b["c"] > b["prev_close"]
            and regime_by_tk.get(b["tk"])
        }

        assert eng_signals == ana_signals, (
            f"{sym}: signal sets differ. engine-only: "
            f"{sorted(eng_signals - ana_signals)}, analysis-only: "
            f"{sorted(ana_signals - eng_signals)}"
        )
