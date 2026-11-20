import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine.config import RiskConfig  # noqa: E402
from engine.risk_gate import AccountState, RiskGate, SessionState  # noqa: E402

from datetime import datetime  # noqa: E402

# Measured 2026-11-19, bad ticks excluded. See docs/API_CAPABILITIES.md s3d.
MEASURED = {
    "US.SPY": (717.56, 1.6840),
    "US.QQQ": (647.00, 2.1417),
    "US.IWM": (264.92, 0.5666),
    "US.XLF": (55.645, 0.0963),
    "US.XLE": (60.9857, 0.1092),
}


@pytest.fixture
def cfg():
    return RiskConfig.load(ROOT / "config" / "risk_limits.yaml")


@pytest.fixture
def gate(cfg):
    return RiskGate(cfg)


@pytest.fixture
def clean_account():
    return AccountState(
        acc_id="5832647",
        account_title="Moomoo x Qoder AI Paper Trading Agent Challenge",
        account_type=2,
        total_asset=100_000.0,
        balance=100_000.0,
        hold=0.0,
        market_value=0.0,
    )


@pytest.fixture
def open_session():
    return SessionState(
        now_et=datetime(2026, 11, 26, 10, 30),
        entries_this_session=0,
        orders_this_session=0,
        seconds_since_last_entry=None,
        seconds_since_last_completed_cycle=45.0,
        session_pnl_pct=0.0,
        peak_drawdown_pct=0.0,
        consecutive_losses=0,
    )
