"""The dashboard's three claims, asserted on the generated HTML.

The dashboard is evidence, so the tests read the artifact a judge would read.
A screenshot proves it rendered; these tests prove what it says.

    1. a recorded hole cuts the series and the gap count matches
    2. a broken chain renders FAILED and nothing else
    3. a complete session draws one unbroken series and reports no gaps
"""

from __future__ import annotations

import re
import sys
from datetime import datetime
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import build_dashboard  # noqa: E402

from engine.coverage import compute_coverage  # noqa: E402
from engine.ledger import Ledger, write_genesis  # noqa: E402

DAY = datetime(2026, 11, 23)

_ALL_TRUE = {
    "dislocated_below_sma20": True,
    "ask_below_threshold": True,
    "ticked_up": True,
    "regime_ok": True,
    "quote_fresh": True,
    "spread_tight": True,
    "within_entry_window": True,
}


def _build(tmp_path: Path, times: list[str], window_end: str,
           coverage: bool = True) -> Path:
    """A small, valid chain: genesis, cycles, snapshots and signals."""
    path = tmp_path / "ledger.jsonl"
    led = Ledger(path)
    write_genesis(
        led, config_hash="c" * 64, strategy_hash="s" * 64,
        code_commit="deadbeef" * 5,
        account={"acc_id": "5832647", "starting_equity_usd": 100000.0},
    )
    for hm in times:
        cid = f"C{DAY:%Y%m%d}T{hm.replace(':', '')}"
        led.append({"cycle_id": cid, "stage": "cycle", "verdict": "NO_ACTION",
                    "reason_code": "CYCLE_COMPLETE", "ts_utc": "2026-11-23T14:00:00+00:00"})
        led.append({"cycle_id": cid, "stage": "account_snapshot", "verdict": "OBSERVED",
                    "reason_code": "EQUITY_SNAPSHOT", "equity": 100000.0,
                    "ts_utc": "2026-11-23T14:00:00+00:00"})
        led.append({"cycle_id": cid, "stage": "signal", "symbol": "US.SPY",
                    "verdict": "NO_SETUP", "reason_code": "NO_SETUP",
                    "reason_text": "failed: dislocated_below_sma20",
                    "conditions": {**_ALL_TRUE, "dislocated_below_sma20": False},
                    "detail": {"close": 700.0, "entry_threshold": 710.0,
                               "atr20": 5.0},
                    "ts_utc": "2026-11-23T14:00:00+00:00"})
    if coverage:
        recs = led.records()
        cov = compute_coverage(
            recs, trading_date=DAY.date(), cadence_seconds=780,
            window_end_et=datetime.fromisoformat(f"2026-11-23T{window_end}"),
        )
        led.append({"cycle_id": f"COVERAGE-{DAY:%Y%m%d}",
                    "stage": "session_coverage", "verdict": cov.verdict,
                    "reason_code": "SESSION_COVERAGE",
                    "reason_text": "test fixture", **cov.to_ledger()})
    return path


def _render(tmp_path: Path, ledger: Path, findings: Path | None = None) -> str:
    out = tmp_path / "dash.html"
    code = build_dashboard.main([
        "--ledger", str(ledger), "--out", str(out),
        "--config", str(ROOT / "config" / "risk_limits.yaml"),
        "--findings", str(findings or tmp_path / "none.md"),
    ])
    assert code in (0, 1)
    return out.read_text()


def _segments(html: str, line_id: str) -> int:
    m = re.search(rf'id="line-{line_id}" data-segments="(\d+)"', html)
    assert m, f"line {line_id} not rendered"
    return int(m.group(1))


def _subpaths(html: str, line_id: str) -> int:
    m = re.search(rf'id="line-{line_id}"[^>]* d="([^"]*)"', html)
    assert m, f"line {line_id} has no path"
    return m.group(1).count("M ")


def test_a_recorded_hole_cuts_the_series_and_the_gap_count_is_its_count(tmp_path):
    ledger = _build(tmp_path, ["09:41", "09:54", "10:45"], window_end="10:45")
    html = _render(tmp_path, ledger)

    assert 'id="panel-equity" data-gaps="1"' in html
    assert 'id="panel-coverage" data-gaps="1"' in html
    assert 'data-actual="3"' in html and 'data-expected="5"' in html
    # The hole is 09:54 -> 10:45 (3060s): two segments, one path, two M commands.
    assert _segments(html, "agent") == 2
    assert _subpaths(html, "agent") == 2
    # d_atr is cut by the same rule.
    assert _segments(html, "s-0") == 2
    assert "3,060" in html


def test_a_broken_chain_renders_failed_and_nothing_else(tmp_path):
    ledger = _build(tmp_path, ["09:41", "09:54"], window_end="09:54")
    lines = ledger.read_text().splitlines()
    idx = next(i for i, ln in enumerate(lines) if '"stage":"cycle"' in ln)
    lines[idx] = lines[idx].replace('"verdict":"NO_ACTION"', '"verdict":"EDITED"')
    ledger.write_text("\n".join(lines) + "\n")

    html = _render(tmp_path, ledger)
    assert "CHAIN VERIFICATION FAILED" in html
    assert "panel-" not in html
    assert "<svg" not in html
    assert "content altered at seq" in html


def test_a_complete_session_has_no_gaps_and_no_breaks(tmp_path):
    ledger = _build(tmp_path, ["09:41", "09:54", "10:07"], window_end="10:07")
    html = _render(tmp_path, ledger)

    assert 'id="panel-equity" data-gaps="0"' in html
    assert 'id="panel-coverage" data-gaps="0"' in html
    assert 'data-actual="3"' in html and 'data-expected="3"' in html
    assert _segments(html, "agent") == 1
    assert _subpaths(html, "agent") == 1
    assert "0 gap(s)" in html


def test_the_output_has_no_network_references_or_scripts(tmp_path):
    ledger = _build(tmp_path, ["09:41", "09:54"], window_end="09:54")
    html = _render(tmp_path, ledger)
    assert "http" not in html
    assert "<script" not in html
    assert "@import" not in html


def test_a_ledger_without_a_coverage_record_draws_points_but_no_line(tmp_path):
    ledger = _build(tmp_path, ["09:41", "09:54"], window_end="09:54",
                    coverage=False)
    html = _render(tmp_path, ledger)
    assert "no session_coverage record for this date" in html
    assert 'id="panel-coverage" data-gaps="missing"' in html
    assert "Coverage\ncannot be computed" in html or "cannot be computed" in html


def test_findings_panel_counts_distinct_ids_and_flags_prose_disagreement(tmp_path):
    reg = tmp_path / "findings.md"
    reg.write_text(
        "| # | Sev | Finding | Disposition |\n"
        "|---|---|---|---|\n"
        "| P-a | High | x | **Fixed.** y |\n"
        "| N1 | Low | x | **Open.** y |\n"
        "| N2 | Med | x | **Closed.** y |\n"
        "| N2 | | x | **Closed.** y again |\n"
        "| N3 | Low | x | **Accepted.** y |\n"
        "\nregister stands at **3** numbered findings.\n",
        encoding="utf-8",
    )
    ledger = _build(tmp_path, ["09:41"], window_end="09:41")
    html = _render(tmp_path, ledger, findings=reg)
    assert "total 4 (3 N-series, 1 P-series)" in html
    assert "open 1" in html and "closed 2" in html
    assert "neither open nor closed 1" in html
    assert "counts 4 distinct IDs" in html and "says 3" in html
