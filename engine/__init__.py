"""Deterministic trading engine core.

Layering, strictest first. Nothing in a lower layer imports a higher one.

    errors      vocabulary of refusal
    ledger      hash-chained append-only record
    config      fail-closed loader for risk_limits.yaml
    account     AccountState built from broker data only, never from config
    provenance  derives and verifies the commit a ledger claims to come from
    sizing      pure function: inputs -> quantity, with its arithmetic
    risk_gate   the last gate; may only tighten or refuse

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
from .sizing import SizingResult, size_position

__all__ = [
    "RiskConfig", "Ledger", "write_genesis", "file_sha256",
    "account_state_from_api", "describe_guard_inputs", "ledger_order_ids",
    "describe_code", "ENGINE_FILES",
    "size_position", "SizingResult",
    "RiskGate", "AccountState", "SessionState", "GateDecision",
    "EngineError", "ConfigError", "AccountGuardError", "IntegrityError",
    "RiskViolation", "StaleDataError",
]
