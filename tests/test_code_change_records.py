"""Engine changes are recorded inside the chain, not inferred from diffs.

Cycle 1 of the live session pinned eb87bb8 and cycles 2-3 pinned 086af1f.
Each record is self-describing, but noticing that the engine changed mid-chain
required diffing commits. A `code_change` record names both commits and both
file-set hashes, and is written once, before the cycle record for the cycle in
which the change is first seen.
"""

from engine.provenance import last_pinned_commit, plan_code_change

COMMIT_A = "a" * 40
COMMIT_B = "b" * 40


def _records(pin):
    return [
        {"stage": "genesis", "provenance": {"code_commit": pin}},
        {"stage": "genesis_provenance", "code_commit": pin},
        {"stage": "cycle", "verdict": "NO_ACTION", "code_commit": pin},
    ]


def test_a_changed_commit_produces_exactly_one_code_change_record():
    records = _records(COMMIT_A)
    change = plan_code_change(records, current_commit=COMMIT_B,
                              current_file_set_hash="h-new",
                              previous_file_set_hash="h-old")
    assert change is not None
    assert change["stage"] == "code_change"
    assert change["reason_code"] == "CODE_COMMIT_CHANGED"
    assert change["from"] == {"code_commit": COMMIT_A,
                              "engine_file_set_hash": "h-old"}
    assert change["to"] == {"code_commit": COMMIT_B,
                            "engine_file_set_hash": "h-new"}

    records.append(change)
    assert sum(1 for r in records if r.get("stage") == "code_change") == 1
    assert plan_code_change(records, current_commit=COMMIT_B,
                            current_file_set_hash="h-new") is None


def test_a_matching_commit_produces_no_record():
    assert plan_code_change(_records(COMMIT_A), current_commit=COMMIT_A,
                            current_file_set_hash="h") is None


def test_an_empty_ledger_produces_no_record():
    assert plan_code_change([], current_commit=COMMIT_A,
                            current_file_set_hash="h") is None


def test_the_pin_is_read_from_the_most_recent_cycle_record():
    records = _records(COMMIT_A) + [
        {"stage": "account_guard", "code_commit": COMMIT_B},
        {"stage": "cycle", "verdict": "NO_ACTION", "code_commit": COMMIT_A},
    ]
    assert last_pinned_commit(records) == COMMIT_A
