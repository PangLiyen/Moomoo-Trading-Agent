"""Hash-chained, append-only decision ledger.

WHAT THIS PROVES, AND WHAT IT DOES NOT

It proves that no record was altered or removed after it was written. Each
entry's hash covers its own content plus the previous entry's hash, so editing
any historical record invalidates every hash after it.

It does NOT prove that the records were truthful when written. A system that
logs a lie logs it into an unbreakable chain. The chain makes tampering
detectable; it does nothing about dishonesty at write time.

That distinction is stated here, in the docstring of the mechanism itself,
because it is the single easiest thing to overclaim in the submission, and
because anyone reading this file is exactly the person who needs to know.

What makes the records trustworthy at write time is separate and structural:
the sizing is computed by formula rather than chosen by a model, the risk
checks run in a process that cannot be prompted, and refusals are written with
the same ceremony as executions. The chain is the audit trail; those are the
reason the audit trail is worth auditing.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .errors import IntegrityError

GENESIS_HASH = "0" * 64

# Fields excluded from the hash preimage: `hash` cannot cover itself, and
# `seq` is derived from position rather than being content.
_UNHASHED = frozenset({"hash"})


def _canonical(record: dict[str, Any]) -> bytes:
    """Serialise deterministically.

    sort_keys and a fixed separator matter: dict ordering or a stray space
    would change the digest without changing the meaning, and the chain would
    fail verification on a file it had itself written.
    """
    payload = {k: v for k, v in record.items() if k not in _UNHASHED}
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def compute_hash(record: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical(record)).hexdigest()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Ledger:
    """Append-only JSONL with a SHA-256 chain.

    Durability: every append is fsync'd before returning. An agent that
    believes it has recorded a decision and then dies before the page cache
    flushes has an account state its ledger cannot explain — which is
    indistinguishable, after the fact, from an unlogged trade.
    """

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._seq, self._tip = self._read_tip()

    # ---------- reading ----------

    def _read_tip(self) -> tuple[int, str]:
        """Return (last seq, last hash), or (0, GENESIS_HASH) for a new file."""
        if not self.path.exists() or self.path.stat().st_size == 0:
            return 0, GENESIS_HASH
        last = None
        for last in self._iter_raw():
            pass
        if last is None:
            return 0, GENESIS_HASH
        return int(last["seq"]), str(last["hash"])

    def _iter_raw(self) -> Iterator[dict[str, Any]]:
        with self.path.open("r", encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                line = line.strip()
                if not line:
                    continue
                try:
                    yield json.loads(line)
                except json.JSONDecodeError as exc:
                    raise IntegrityError(
                        f"{self.path}:{lineno} is not valid JSON: {exc}"
                    ) from exc

    def records(self) -> list[dict[str, Any]]:
        return list(self._iter_raw())

    @property
    def seq(self) -> int:
        return self._seq

    @property
    def tip(self) -> str:
        return self._tip

    # ---------- writing ----------

    def append(self, record: dict[str, Any]) -> dict[str, Any]:
        """Chain a record and write it durably. Returns the stored record.

        The caller's dict is not mutated — a caller that reuses a template
        dict across cycles would otherwise accumulate stale hashes.
        """
        if "hash" in record or "prev_hash" in record or "seq" in record:
            raise IntegrityError(
                "seq, prev_hash and hash are assigned by the ledger; "
                "a caller supplying them can forge chain position"
            )

        entry = dict(record)
        entry["seq"] = self._seq + 1
        entry["prev_hash"] = self._tip
        entry.setdefault("ts_utc", utc_now_iso())
        entry["hash"] = compute_hash(entry)

        line = json.dumps(entry, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False) + "\n"
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())

        self._seq, self._tip = entry["seq"], entry["hash"]
        return entry

    # ---------- verification ----------

    def verify(self) -> None:
        """Walk the chain. Raise IntegrityError on the first break.

        Checks three distinct failure modes, because they have different
        causes: a wrong self-hash means the content was edited, a wrong
        prev_hash means a record was removed or reordered, and a seq gap means
        a write was lost.
        """
        expected_prev = GENESIS_HASH
        expected_seq = 1

        for rec in self._iter_raw():
            for field in ("seq", "prev_hash", "hash"):
                if field not in rec:
                    raise IntegrityError(
                        f"record {rec.get('seq', '?')} is missing {field!r}"
                    )

            if rec["seq"] != expected_seq:
                raise IntegrityError(
                    f"sequence break: expected seq {expected_seq}, "
                    f"found {rec['seq']} — a record was lost or reordered"
                )
            if rec["prev_hash"] != expected_prev:
                raise IntegrityError(
                    f"chain break at seq {rec['seq']}: prev_hash is "
                    f"{rec['prev_hash'][:12]}..., expected "
                    f"{expected_prev[:12]}... — a preceding record was "
                    f"altered or removed"
                )
            actual = compute_hash(rec)
            if actual != rec["hash"]:
                raise IntegrityError(
                    f"content altered at seq {rec['seq']}: stored hash "
                    f"{rec['hash'][:12]}... does not match recomputed "
                    f"{actual[:12]}..."
                )

            expected_prev = rec["hash"]
            expected_seq += 1

    def verify_report(self) -> dict[str, Any]:
        """Verification as data rather than an exception, for the dashboard."""
        try:
            self.verify()
        except IntegrityError as exc:
            return {"intact": False, "error": str(exc), "records": self._seq}
        return {
            "intact": True,
            "records": self._seq,
            "tip": self._tip,
            "verified_at_utc": utc_now_iso(),
        }


def write_genesis(ledger: Ledger, *, config_hash: str, strategy_hash: str,
                  code_commit: str, account: dict[str, Any]) -> dict[str, Any]:
    """First record: what the system was when it started.

    Pins the rules and the code to the chain's root. A judge can then confirm
    that the config in the repo is the config the agent actually ran, rather
    than taking the repo's word for it.
    """
    return ledger.append({
        "stage": "genesis",
        "verdict": "INIT",
        "reason_code": "SESSION_START",
        "reason_text": "Engine initialised; rules and code pinned to chain root.",
        "provenance": {
            "rules_hash": config_hash,
            "strategy_hash": strategy_hash,
            "code_commit": code_commit,
        },
        "account_snapshot": account,
    })


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()
