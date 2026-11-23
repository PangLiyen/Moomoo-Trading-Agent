"""Deterministic trading engine core.

Layering, strictest first. Nothing in a lower layer imports a higher one.

    errors      vocabulary of refusal
    ledger      hash-chained append-only record
    config      fail-closed loader for risk_limits.yaml
    account     AccountState built from broker data only, never from config
    provenance  derives and verifies the commit a ledger claims to come from
    bars        RTH and interval validation at the data boundary
    signal      indicators and the entry/exit rules
    sizing      pure function: inputs -> quantity, with its arithmetic
    risk_gate   the last gate; may only tighten or refuse
    session_state  live state derived from the ledger, never a side file
    executor    exits, order intents, and the records that make the
                pre-registered predictions measurable. No network access:
                it emits intents, it cannot submit them

No module here performs network I/O, reads free text, or consults a model.
That is what makes the limits enforceable rather than advisory.
"""

from .account import account_state_from_api, describe_guard_inputs, ledger_order_ids
from .config import RiskConfig
from .errors import (
    AccountGuardError,
    ConfigError,
    EngineError,
    IntegrityError,
    RiskViolation,
    StaleDataError,
)
from .ledger import Ledger, file_sha256, write_genesis
from .provenance import ENGINE_FILES, describe_code
from .risk_gate import AccountState, GateDecision, RiskGate, SessionState
from .bars import BarAudit, validate as validate_bars
from .executor import (
    ExitIntent, SessionPlan, build_order, crossing_limit, plan_exits,
    plan_session, record_account_snapshot, record_exit, record_fill,
    record_submission, write_intents,
)
from .session_state import LiveState, reconstruct
from .sizing import SizingResult, size_position

__all__ = [
    "RiskConfig", "Ledger", "write_genesis", "file_sha256",
    "account_state_from_api", "describe_guard_inputs", "ledger_order_ids",
    "describe_code", "ENGINE_FILES",
    "validate_bars", "BarAudit",
    "reconstruct", "LiveState",
    "plan_session", "SessionPlan", "plan_exits", "ExitIntent",
    "build_order", "crossing_limit", "write_intents",
    "record_submission", "record_fill", "record_exit",
    "record_account_snapshot",
    "size_position", "SizingResult",
    "RiskGate", "AccountState", "SessionState", "GateDecision",
    "EngineError", "ConfigError", "AccountGuardError", "IntegrityError",
    "RiskViolation", "StaleDataError",
]
