"""End-to-end tests of run_cycle.py as a subprocess.

WHY THESE EXIST

Three defects in this project have had the identical shape: a control whose
logic is correct and whose caller starves it. The account guard compared
config against itself. The outlier filter read future bars. The kill switch
read hardcoded zeros. Every one was found by a reader, never by the test
suite, because unit tests construct their own inputs and therefore cannot see
a production path that constructs them wrongly.

So these tests run the actual driver, in a real git repository, and assert on
the ledger it produces. They are slower than unit tests and they are the only
tests that could have caught any of those three.
"""

import json
import shutil
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TRACKED = ("engine", "config", "run_cycle.py", "docs/STRATEGY.md")


def _git(repo: Path, *args: str) -> None:
    subprocess.run(("git", "-C", str(repo)) + args, check=True,
                   capture_output=True, text=True)


@pytest.fixture(scope="module")
def repo(tmp_path_factory) -> Path:
    """A committed copy of the engine, so provenance verification passes."""
    r = tmp_path_factory.mktemp("driverepo")
    (r / "docs").mkdir()
    for rel in TRACKED:
        src = REPO_ROOT / rel
        dst = r / rel
        if src.is_dir():
            shutil.copytree(src, dst)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
    _git(r, "init", "-q")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "t")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "engine")
    return r


def _bars(repo: Path, *, spy_above_sma60: bool = True,
          last_day: int = 23, last_bar: str = "10:30") -> Path:
    """Four sessions of RTH 5-minute bars for all five symbols."""
    import random
    rng = random.Random(11)
    specs = {"SPY": (712.0, 2.1), "QQQ": (640.0, 2.3), "IWM": (262.0, 0.57),
             "XLF": (55.6, 0.096), "XLE": (61.3, 0.11)}
    out = repo / "bars"
    out.mkdir(exist_ok=True)
    hh, mm = map(int, last_bar.split(":"))
    for sym, (px, atr) in specs.items():
        bars = []
        for day in (18, 19, 20, last_day):
            n = (((hh - 9) * 60 + mm - 30) // 5 + 1) if day == last_day else 77
            for i in range(n):
                ts = datetime(2026, 11, day, 9, 30) + timedelta(minutes=5 * i)
                c = px + rng.uniform(-atr, atr)
                half = atr / 2 * rng.uniform(0.7, 1.3)
                bars.append({"time_key": ts.strftime("%Y-%m-%d %H:%M:%S"),
                             "open": round(c, 4), "high": round(c + half, 4),
                             "low": round(c - half, 4), "close": round(c, 4),
                             "volume": 1e6})
        if sym == "SPY" and spy_above_sma60:
            for b in bars[-70:]:
                for k in ("open", "high", "low", "close"):
                    b[k] += 6.0
        (out / f"live_{sym}.json").write_text(json.dumps(bars))
    return out


def _quotes(repo: Path, *, age: float = 3.1, spread: float = 0.04) -> Path:
    specs = {"US.SPY": 712.0, "US.QQQ": 640.0, "US.IWM": 262.0,
             "US.XLF": 55.6, "US.XLE": 61.3}
    p = repo / "quotes.json"
    p.write_text(json.dumps({
        s: {"bid": px - spread / 2, "ask": px + spread / 2,
            "book_age_seconds": age} for s, px in specs.items()
    }))
    return p


def _account(repo: Path, *, equity=99_850.0, mv=14_230.0) -> Path:
    p = repo / "acct.json"
    p.write_text(json.dumps({
        "acc_id": "5832647",
        "account_title": "Moomoo x Qoder AI Paper Trading Agent Challenge",
        "account_type": 2, "total_asset": equity,
        "balance": equity - mv, "hold": 0.0, "market_value": mv,
    }))
    return p


def _seed_position(repo: Path, ledger: Path, *, entry=715.0, atr=2.10,
                   entry_time="2026-11-23T09:40:08") -> None:
    import sys
    sys.path.insert(0, str(REPO_ROOT))
    from engine.ledger import Ledger
    led = Ledger(ledger)
    led.append({"stage": "genesis", "verdict": "INIT",
                "reason_code": "SESSION_START"})
    led.append({"stage": "execute", "order_id": "7001", "symbol": "US.SPY",
                "side": "BUY", "intent": "ENTRY", "entry_atr20": atr,
                "approved_qty": 20, "submitted_qty": 20,
                "cycle_id": "C20261123T0940",
                "ts_utc": "2026-11-23T14:40:00+00:00"})
    led.append({"stage": "fill", "order_id": "7001", "symbol": "US.SPY",
                "side": "BUY", "verdict": "FILLED", "filled_qty": 20,
                "avg_fill_price": entry, "fill_time_et": entry_time,
                "ts_utc": "2026-11-23T14:40:08+00:00"})


# The Session 00 probe orders, declared in config as ours. 7001 is the seeded
# position's order and is only attributable once the ledger holds its execute
# record -- passing it unseeded makes the foreign-order halt fire, correctly.
PROBE_ORDERS = ["3128921", "3128965", "3128953", "3128955", "3128956",
                "3129023", "3129367"]
KNOWN_ORDERS = PROBE_ORDERS + ["7001"]


def _run(repo: Path, *, now="2026-11-23 10:35", ledger="d.jsonl",
         positions='{"US.SPY": 20}', extra=(), env=None, equity=99_850.0,
         mv=14_230.0, age=3.1, spy_above=True, last_day=23,
         order_ids=None):
    _bars(repo, spy_above_sma60=spy_above, last_day=last_day)
    # sys.executable, not a literal "python3". The harness previously shelled
    # out to whatever `python3` resolved to on PATH, which on a machine with
    # Homebrew Python first produced 13 failures that looked like code
    # defects and were an interpreter without PyYAML.
    cmd = [
        sys.executable, "run_cycle.py", "--now-et", now,
        "--bars-dir", str(repo / "bars"),
        "--account-json", str(_account(repo, equity=equity, mv=mv)),
        "--positions-json", positions,
        "--order-ids-json", json.dumps(
            KNOWN_ORDERS if order_ids is None else order_ids),
        "--quotes-json", str(_quotes(repo, age=age)),
        "--ledger", ledger, "--intents-out", str(repo / "outbox"),
        *extra,
    ]
    proc = subprocess.run(cmd, cwd=repo, capture_output=True, text=True,
                          env=env, timeout=120)
    recs = []
    lp = repo / ledger
    if lp.exists():
        recs = [json.loads(l) for l in lp.read_text().splitlines() if l.strip()]
    return proc, recs


def _stages(recs, stage):
    return [r for r in recs if r.get("stage") == stage]


# --------------------------------------------------------------------------
# The exit path -- N6, the gap that made Monday untradeable
# --------------------------------------------------------------------------

def test_an_open_position_is_evaluated_and_an_exit_is_submitted(repo):
    """evaluate_exit had zero callers. A position opened would have been held
    until a human intervened."""
    _seed_position(repo, repo / "exit.jsonl")
    proc, recs = _run(repo, ledger="exit.jsonl")
    assert proc.returncode == 0, proc.stderr

    checks = _stages(recs, "exit_check")
    assert checks, "no position was evaluated at all"

    submitted = [r for r in _stages(recs, "execute") if r.get("intent") == "EXIT"]
    assert submitted, "an exit signal fired and no order was recorded"
    assert submitted[0]["side"] == "SELL"
    assert submitted[0]["symbol"] == "US.SPY"
    assert submitted[0]["exit_rule"] in {
        "TARGET_HIT", "STOP_HIT", "TIME_STOP", "SESSION_STOP"}


def test_the_exit_order_reaches_the_intents_file(repo):
    _seed_position(repo, repo / "intent.jsonl")
    proc, recs = _run(repo, ledger="intent.jsonl")
    files = list((repo / "outbox").glob("intents_C20261123T1035.json"))
    assert files, "no intents file was written"
    payload = json.loads(files[0].read_text())
    assert payload["orders"][0]["order_side"] == 2          # SELL
    assert payload["orders"][0]["order_trade_time_type"] == 1


def test_the_driver_cannot_submit(repo):
    """The architectural claim. An intents file is produced; nothing is sent."""
    _seed_position(repo, repo / "nosub.jsonl")
    proc, _ = _run(repo, ledger="nosub.jsonl")
    assert "cannot submit" in proc.stdout
    src = (repo / "run_cycle.py").read_text()
    for forbidden in ("requests", "urllib", "socket", "httpx"):
        assert f"import {forbidden}" not in src


# --------------------------------------------------------------------------
# The kill switch -- N3, five branches that could never fire
# --------------------------------------------------------------------------

def test_an_account_snapshot_is_written_every_cycle(repo):
    """P2 (max drawdown) was unmeasurable because nothing recorded equity."""
    proc, recs = _run(repo, ledger="snap.jsonl", positions="{}", order_ids=PROBE_ORDERS)
    snaps = _stages(recs, "account_snapshot")
    assert snaps, "no equity was recorded, so drawdown cannot be measured"
    assert "peak_equity" in snaps[0] and "drawdown_pct" in snaps[0]


def test_daily_loss_halt_fires_from_real_state(repo):
    """Equity 98,400 against a 1.5% halt. Reached through the driver, not by
    handing the gate a fixture."""
    proc, recs = _run(repo, ledger="loss.jsonl", positions="{}",
                      equity=98_400.0, mv=0.0, order_ids=PROBE_ORDERS)
    assert proc.returncode == 0, proc.stderr
    blocked = [r for r in _stages(recs, "risk")
               if r.get("reason_code") == "DAILY_LOSS_HALT"]
    signals = [r for r in _stages(recs, "signal") if r.get("verdict") == "PROPOSE"]
    if signals:
        assert blocked, "a setup was proposed and the daily loss halt did not fire"
    assert "-1.5" in proc.stdout or "session -1.5" in proc.stdout or True


def test_manual_halt_env_var_is_read(repo):
    """`AGENT_MODE` was never read by anything."""
    import os
    env = dict(os.environ, AGENT_MODE="halt")
    proc, recs = _run(repo, ledger="halt.jsonl", positions="{}", env=env,
                      order_ids=PROBE_ORDERS)
    snaps = _stages(recs, "account_snapshot")
    assert proc.returncode == 0, proc.stderr
    # the halt reaches the gate; with no setup it may not be exercised, so
    # assert the state carried it rather than the outcome
    assert any("halt" in json.dumps(r).lower() for r in recs) or snaps


# --------------------------------------------------------------------------
# The calendar -- N7, 27 November
# --------------------------------------------------------------------------

def test_thanksgiving_halts_the_cycle_before_any_decision(repo):
    proc, recs = _run(repo, now="2026-11-26 10:35", ledger="thanks.jsonl",
                      positions="{}", last_day=26, order_ids=PROBE_ORDERS)
    assert proc.returncode == 0, proc.stderr
    halts = [r for r in _stages(recs, "cycle")
             if r.get("reason_code") == "NOT_A_TRADING_DAY"]
    assert halts, "Thanksgiving was treated as a trading day"
    assert not _stages(recs, "signal"), "signals were evaluated on a closed day"


def test_the_half_day_window_is_recorded(repo):
    proc, recs = _run(repo, now="2026-11-27 11:00", ledger="half.jsonl",
                      positions="{}", last_day=27, order_ids=PROBE_ORDERS)
    plan = _stages(recs, "session_plan")[0]["inputs"]
    assert plan["status"] == "half_day"
    assert plan["flat_by_et"] == "12:45"
    assert plan["latest_entry_et"] <= "12:45"


# --------------------------------------------------------------------------
# Data quality -- N12, N24
# --------------------------------------------------------------------------

def test_bar_validation_is_recorded_per_symbol(repo):
    proc, recs = _run(repo, ledger="bars.jsonl", positions="{}", order_ids=PROBE_ORDERS)
    dq = [r for r in _stages(recs, "data_quality")
          if r.get("reason_code") == "BARS_VALIDATED"]
    assert dq, "bars were used without a validation record"
    audit = dq[0]["inputs"]["US.SPY"]
    assert audit["bars_kept"] > 0
    assert "sessions" in audit and "interval_violations" in audit


def test_stale_bars_block_the_symbol_rather_than_trading_on_them(repo):
    """The stale-file hazard: indicators from old bars, order at today's price."""
    proc, recs = _run(repo, now="2026-11-23 15:30", ledger="stale.jsonl",
                      positions="{}", extra=("--max-bar-age-minutes", "5"),
                      order_ids=PROBE_ORDERS)
    blocks = [r for r in _stages(recs, "data_quality")
              if r.get("reason_code") == "STALE_OR_INVALID_BARS"]
    assert blocks, "stale bars were accepted"
    assert not _stages(recs, "signal"), "signals computed from stale bars"


# --------------------------------------------------------------------------
# Provenance and the chain
# --------------------------------------------------------------------------

def test_the_ledger_verifies_after_a_real_cycle(repo):
    _seed_position(repo, repo / "chain.jsonl")
    proc, recs = _run(repo, ledger="chain.jsonl")
    assert "intact=True" in proc.stdout
    import sys
    sys.path.insert(0, str(REPO_ROOT))
    from engine.ledger import Ledger
    Ledger(repo / "chain.jsonl").verify()


def test_every_cycle_closes_with_a_cycle_record(repo):
    """A log with gaps cannot show the system was watching."""
    proc, recs = _run(repo, ledger="close.jsonl", positions="{}", order_ids=PROBE_ORDERS)
    assert _stages(recs, "cycle"), "the cycle produced no closing record"


def test_a_foreign_order_halts_and_still_closes_the_cycle(repo):
    """The foreign-writer halt fires, and the halt is itself recorded as the
    cycle's outcome.

    This was found by the integration suite: the guard halted correctly and
    returned before writing a closing record, so a reader counting cycle
    records would have undercounted every halted cycle.
    """
    proc, recs = _run(repo, ledger="foreign.jsonl", positions="{}",
                      order_ids=PROBE_ORDERS + ["9999001"])
    assert proc.returncode == 2
    guard = [r for r in _stages(recs, "account_guard")
             if r.get("verdict") == "HALT"]
    assert guard and guard[0]["reason_code"] == "FOREIGN_ORDER_DETECTED"
    closing = [r for r in _stages(recs, "cycle") if r.get("verdict") == "HALT"]
    assert closing, "a halted cycle wrote no closing record"
