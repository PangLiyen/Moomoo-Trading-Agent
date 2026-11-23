"""ledger_order_ids stays narrow: only records that say `order_id`.

Guard records echo the full broker order list into `inputs.from_api`. If that
scan were ever made recursive, every foreign order would attribute itself as
ours the first time it appeared in one guard record -- and the second-writer
guard, the check that caught untagged type-2 orders on 2026-11-20, would
silently stop working. The fixture is the real pair.
"""

from engine.account import ledger_order_ids

ANT_ORDER_IDS = ("3128921", "3128965")


def _guard_echo():
    return {
        "stage": "account_guard",
        "verdict": "APPROVE",
        "inputs": {
            "from_api": {
                "orders": [{"order_id": oid, "symbol": "ANT", "text": ""}
                           for oid in ANT_ORDER_IDS]
            }
        },
    }


def test_ids_that_exist_only_inside_a_guard_echo_are_not_ours():
    records = [_guard_echo()]
    assert ledger_order_ids(records) == set()


def test_a_top_level_order_id_is_ours():
    assert ledger_order_ids([{"order_id": "3129367"}]) == {"3129367"}


def test_an_echo_does_not_launder_a_foreign_id_into_ours():
    records = [_guard_echo(), {"stage": "submission", "order_id": "3129367"}]
    assert ledger_order_ids(records) == {"3129367"}
    assert not set(ANT_ORDER_IDS) & ledger_order_ids(records)
