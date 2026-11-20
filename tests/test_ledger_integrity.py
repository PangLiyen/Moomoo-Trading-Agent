"""The ledger's claim is narrow and specific: no record can be altered or
removed without detection. These tests attack the chain the way someone
dressing up a result would, and assert that the chain notices.

One test asserts a LIMIT of the claim rather than a strength. A suite that
only demonstrates what a mechanism does well misrepresents it.
"""

import json

import pytest

from engine.errors import IntegrityError
from engine.ledger import GENESIS_HASH, Ledger, compute_hash


@pytest.fixture
def ledger(tmp_path):
    led = Ledger(tmp_path / "decisions.jsonl")
    led.append({"stage": "genesis", "verdict": "INIT", "reason_code": "SESSION_START"})
    led.append({"stage": "signal", "verdict": "NO_SETUP", "symbol": "US.SPY"})
    led.append({"stage": "risk", "verdict": "APPROVE", "symbol": "US.QQQ", "qty": 23})
    led.append({"stage": "order", "verdict": "FILLED", "symbol": "US.QQQ", "fill": 647.01})
    return led


def _rewrite(path, records):
    path.write_text(
        "".join(
            json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n" for r in records
        ),
        encoding="utf-8",
    )


def test_intact_chain_verifies(ledger):
    ledger.verify()
    assert ledger.verify_report()["intact"] is True
    assert ledger.seq == 4


def test_genesis_record_anchors_to_zero(ledger):
    assert ledger.records()[0]["prev_hash"] == GENESIS_HASH


def test_editing_a_fill_price_is_detected(ledger):
    """The specific fraud this defends against: improving a recorded fill."""
    recs = ledger.records()
    recs[3]["fill"] = 650.00
    _rewrite(ledger.path, recs)

    with pytest.raises(IntegrityError, match="content altered at seq 4"):
        Ledger(ledger.path).verify()


def test_editing_and_rehashing_one_record_is_still_detected(ledger):
    """A careful forger recomputes the hash of the record they edit. That
    defeats the self-hash check but not the chain: every later record still
    carries the old prev_hash."""
    recs = ledger.records()
    recs[1]["verdict"] = "APPROVE"
    recs[1]["hash"] = compute_hash(recs[1])
    _rewrite(ledger.path, recs)

    with pytest.raises(IntegrityError, match="chain break at seq 3"):
        Ledger(ledger.path).verify()


def test_deleting_an_inconvenient_record_is_detected(ledger):
    """Removing a loss, or a BLOCK that reveals a near-miss."""
    recs = ledger.records()
    del recs[2]
    _rewrite(ledger.path, recs)

    with pytest.raises(IntegrityError, match="sequence break"):
        Ledger(ledger.path).verify()


def test_tail_truncation_is_NOT_detectable_internally(ledger):
    """An honest limitation, asserted rather than hidden.

    Dropping records from the END leaves a self-consistent chain: seq runs
    1..n and every prev_hash matches. Internal verification cannot detect it.
    Detection needs an external anchor, which is why each order's ledger hash
    is stamped into the broker-side `text` field where we cannot edit it.
    """
    _rewrite(ledger.path, ledger.records()[:2])

    Ledger(ledger.path).verify()  # passes -- and that is the finding
    assert Ledger(ledger.path).seq == 2


def test_reordering_records_is_detected(ledger):
    recs = ledger.records()
    recs[1], recs[2] = recs[2], recs[1]
    _rewrite(ledger.path, recs)

    with pytest.raises(IntegrityError):
        Ledger(ledger.path).verify()


def test_caller_cannot_forge_chain_position(ledger):
    """A caller that can set seq, prev_hash or hash can insert a record
    anywhere in history, or overwrite one."""
    for forged in ({"seq": 1}, {"prev_hash": "0" * 64}, {"hash": "f" * 64}):
        with pytest.raises(IntegrityError, match="assigned by the ledger"):
            ledger.append({"stage": "x", **forged})


def test_appends_resume_correctly_after_reopen(ledger):
    """An agent restarted mid-session must not restart the chain."""
    tip, seq = ledger.tip, ledger.seq
    reopened = Ledger(ledger.path)
    assert (reopened.tip, reopened.seq) == (tip, seq)

    reopened.append({"stage": "signal", "verdict": "NO_SETUP"})
    reopened.verify()
    assert reopened.seq == seq + 1


def test_caller_dict_is_not_mutated(ledger):
    """A caller reusing a template dict would otherwise carry a stale hash
    into the next record."""
    template = {"stage": "signal", "verdict": "NO_SETUP"}
    ledger.append(template)
    assert template == {"stage": "signal", "verdict": "NO_SETUP"}


def test_corrupt_json_names_the_line(ledger):
    with ledger.path.open("a", encoding="utf-8") as fh:
        fh.write("{not json\n")
    with pytest.raises(IntegrityError, match=":5"):
        Ledger(ledger.path).verify()
