"""Exception types. Every one of these means 'do not trade'.

The design rule: there is no exception in this package that a caller can
reasonably catch and continue past. If something here raises, the correct
response is to log it to the ledger and halt. Catching and retrying is how
fail-closed systems quietly become fail-open ones.
"""


class EngineError(Exception):
    """Base. Never raised directly."""


class ConfigError(EngineError):
    """risk_limits.yaml is missing, malformed, or internally inconsistent."""


class AccountGuardError(EngineError):
    """The account is not the competition account, or cannot be proven to be."""


class IntegrityError(EngineError):
    """The ledger hash chain is broken, or the account contains activity we
    cannot attribute to our own decisions."""


class RiskViolation(EngineError):
    """A proposal failed a risk check. Carries the reason code that goes in
    the ledger, so the refusal is as auditable as an execution."""

    def __init__(self, reason_code: str, detail: str = ""):
        self.reason_code = reason_code
        self.detail = detail
        super().__init__(f"{reason_code}: {detail}" if detail else reason_code)


class StaleDataError(EngineError):
    """Market data is too old, or its freshness cannot be established."""
