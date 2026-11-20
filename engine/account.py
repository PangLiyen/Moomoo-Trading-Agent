"""Building an AccountState from what the API actually returned.

WHY THIS MODULE EXISTS

The first version of the cycle driver built its AccountState out of the config
file, then handed it to the account guard, which compared it against the same
config file. Every check passed, always. It could only have failed if the YAML
contradicted itself -- never because the broker returned a different account.
The guard that exists because an agent once traded the wrong account was,
in the live path, checking nothing.

The fix is structural rather than careful: the only way to get an AccountState
is to pass the broker's own response, and every field it needs is mandatory.
A caller that cannot supply what the API said cannot build the object, so the
cycle refuses to run instead of running blind.

This is the same principle as the config loader. Absence is not permission. A
driver that defaults `open_positions` to `{}` has silently asserted the
account is flat, which is a claim about the world that no one checked -- and
on 2026-11-20 it was false: a probe order had filled and we held 1 SPY while
the gate believed we held nothing.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from .config import RiskConfig
from .errors import AccountGuardError
from .risk_gate import AccountState

# Mandatory. Listed rather than inferred so a renamed API field fails loudly
# at the boundary instead of arriving as a zero.
_REQUIRED = ("acc_id", "account_title", "account_type",
             "total_asset", "balance", "hold", "market_value")


def account_state_from_api(
    payload: Mapping[str, Any],
    *,
    positions: Mapping[str, int],
    account_order_ids: Iterable[str],
    ledger_order_ids: Iterable[str],
) -> AccountState:
    """Construct an AccountState from broker data only.

    `payload` must carry the values the API returned -- never values copied
    from config, which is what made the guard vacuous. `positions` must be
    supplied explicitly, even when empty, because "we hold nothing" is an
    assertion that has to come from the position endpoint rather than from a
    default argument.

    Unattributed orders are computed here, as the set difference between what
    the account holds and what our ledger can account for. That set is the
    input to the foreign-writer halt, so leaving the caller to assemble it is
    how it ends up as an empty tuple forever.
    """
    missing = [k for k in _REQUIRED if k not in payload or payload[k] is None]
    if missing:
        raise AccountGuardError(
            f"account payload is missing {missing}. These must come from the "
            f"broker's own response; filling them from config would make the "
            f"account guard compare config against itself and always pass."
        )

    try:
        total = float(payload["total_asset"])
        balance = float(payload["balance"])
        hold = float(payload["hold"])
        mv = float(payload["market_value"])
    except (TypeError, ValueError) as exc:
        raise AccountGuardError(f"non-numeric account figure: {exc}") from exc

    ours = {str(o) for o in ledger_order_ids}
    unattributed = tuple(sorted(str(o) for o in account_order_ids
                                if str(o) not in ours))

    return AccountState(
        acc_id=str(payload["acc_id"]),
        account_title=str(payload["account_title"]),
        account_type=int(payload["account_type"]),
        total_asset=total,
        balance=balance,
        hold=hold,
        market_value=mv,
        open_positions={str(k): int(v) for k, v in positions.items() if int(v) != 0},
        unattributed_order_ids=unattributed,
    )


def ledger_order_ids(records: Iterable[Mapping[str, Any]]) -> set[str]:
    """Order ids our own ledger can account for.

    Read from the chain rather than kept in a side file: the ledger is the
    record of what we did, so anything it does not mention is, by definition,
    not ours.
    """
    found: set[str] = set()
    for rec in records:
        for key in ("order_id", "broker_order_id"):
            if rec.get(key):
                found.add(str(rec[key]))
        order = rec.get("order")
        if isinstance(order, Mapping) and order.get("order_id"):
            found.add(str(order["order_id"]))
    return found


def describe_guard_inputs(acct: AccountState, cfg: RiskConfig) -> dict[str, Any]:
    """What the guard compared, both sides, for the ledger.

    Recording only "guard passed" would leave no way to tell a real comparison
    from a vacuous one -- which is exactly the bug this module was written to
    fix. Logging both sides makes a future tautology visible in the evidence.
    """
    return {
        "from_api": {
            "acc_id": acct.acc_id,
            "account_title": acct.account_title,
            "account_type": acct.account_type,
        },
        "from_config": {
            "acc_id": cfg.acc_id,
            "account_title": cfg.account_name,
            "account_type": cfg.account_type,
        },
        "sources_independent": True,
        "reconciles": acct.reconciles(),
        "open_positions": dict(acct.open_positions),
        "unattributed_order_ids": list(acct.unattributed_order_ids),
    }
