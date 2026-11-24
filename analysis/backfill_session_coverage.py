#!/usr/bin/env python3
"""Append a reconstructed session_coverage record for a past trading date.

WHY THIS EXISTS

`schema.session_coverage` is written by the cycle at flat-by. The 2026-11-23
session never reached its close cycle, so no record exists. The gap is real
and has to be in the chain, not only in a commit message: the chain is what
a judge reads, and "we said so in the commit" is a claim, not a record.

The record is marked `basis: reconstructed` and carries the commit the
analysis was run against, so no one can later mistake it for a live
observation. It is computed by the same `engine.coverage` function the driver
uses -- one implementation, so the reconstructable answer and the live answer
cannot drift apart the way the two bar-selection paths did (N35).

    python3 analysis/backfill_session_coverage.py --date 2026-11-23
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine import (  # noqa: E402
    Ledger, RiskConfig, compute_coverage, plan_session,
)


def _ledger_commit(rel: str) -> str | None:
    try:
        return subprocess.run(
            ["git", "-C", str(ROOT), "log", "-1", "--format=%H", "--", rel],
            capture_output=True, text=True, check=True).stdout.strip() or None
    except (OSError, subprocess.CalledProcessError):
        return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ledger", default="logs/decisions_live.jsonl")
    ap.add_argument("--config", default="config/risk_limits.yaml")
    ap.add_argument("--date", required=True, help="YYYY-MM-DD")
    args = ap.parse_args()

    cfg = RiskConfig.load(ROOT / args.config)
    day = datetime.strptime(args.date, "%Y-%m-%d").date()
    plan = plan_session(cfg, datetime.combine(day, datetime.min.time()).replace(hour=10))
    if not plan.is_trading_day:
        raise SystemExit(f"{day} is {plan.status}: nothing to cover")

    led = Ledger(ROOT / args.ledger)
    records = led.records()
    existing = [r for r in records if r.get("stage") == "session_coverage"
                and r.get("trading_date") == day.isoformat()]
    if existing:
        raise SystemExit(
            f"{day} already has a session_coverage record (basis "
            f"{existing[0].get('basis')!r}); refusing to append a second")

    cadence = float(cfg.section("frequency")["cycle_cadence_seconds"])
    coverage = compute_coverage(
        records, trading_date=day, cadence_seconds=cadence,
        window_end_et=datetime.combine(day, plan.flat_by_et),
        basis="reconstructed",
    )
    body = coverage.to_ledger()
    rec = led.append({
        "cycle_id": f"COVERAGE-{day:%Y%m%d}",
        "stage": "session_coverage",
        "verdict": coverage.verdict,
        "reason_code": "SESSION_COVERAGE",
        "reason_text": (
            f"reconstructed on {datetime.now():%Y-%m-%d} from the committed "
            f"ledger: {coverage.actual_cycles} cycles against "
            f"{coverage.expected_slots} expected slots at {cadence:.0f}s, "
            f"{len(coverage.gaps)} gap(s) over cadence, longest "
            f"{coverage.longest_gap_seconds:.0f}s. No live record exists: the "
            f"session's close cycle never ran"),
        "written_after_the_fact": True,
        "source_ledger_commit": _ledger_commit(str(Path(args.ledger))),
        **body,
    })
    print(f"appended seq {rec['seq']} hash {rec['hash'][:16]}...")
    print(f"  {coverage.verdict}: {coverage.actual_cycles} of "
          f"{coverage.expected_slots} slots, missing {coverage.missing_slots}, "
          f"longest gap {coverage.longest_gap_seconds:.0f}s")
    for g in coverage.gaps:
        print(f"  gap {g['from_et']} -> {g['to_et']}  {g['seconds']:.0f}s")
    report = led.verify_report()
    print(f"ledger: {report['records']} records, intact={report['intact']}")
    return 0 if report["intact"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
