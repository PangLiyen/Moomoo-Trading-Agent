"""Establishing what code actually produced a ledger.

WHY THIS MODULE EXISTS

The genesis record of the first ledger pinned `code_commit: "5748462"`. No
engine file exists at that commit -- they were added in `0a91ffc`. The operator
had passed `git rev-parse HEAD` at a moment when the engine was still
uncommitted, so the record named the commit of the specification and claimed,
by `ledger.py`'s own docstring, to pin "the rules and the code."

The mechanism recorded faithfully what it was handed. That is the failure: a
field accepting any string cannot distinguish a commit that contains the
engine from one that does not, and a wrong value there looks exactly like a
right one.

So the commit is no longer supplied by hand. It is derived, and then verified
against the files it is supposed to describe.

A SECOND, SUBTLER PROBLEM

Even a correct commit does not describe the running code if the working tree
is dirty. Every engine file could be modified on disk and the commit hash
would still verify. So the tree's cleanliness is recorded too, per-file, and a
dirty engine is reported rather than quietly pinned.
"""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any, Iterable, Mapping

from .errors import ConfigError

# Every file that decides or records anything, plus the rules and the driver.
# Listed explicitly rather than globbed: a glob passes on an empty directory,
# and the whole point is to catch a commit that does not contain the engine.
#
# The first version of this tuple omitted errors.py, provenance.py and
# run_cycle.py while the register claimed the check covered "every engine
# file". A commit holding only the eight listed paths would have passed. The
# list is now exhaustive, and `test_engine_files_covers_the_package` fails if
# a new module is added to engine/ without being added here.
ENGINE_FILES = (
    "engine/__init__.py",
    "engine/errors.py",
    "engine/ledger.py",
    "engine/config.py",
    "engine/account.py",
    "engine/provenance.py",
    "engine/sizing.py",
    "engine/signal.py",
    "engine/bars.py",
    "engine/session_state.py",
    "engine/coverage.py",
    "engine/untrusted.py",
    "engine/timeutil.py",
    "engine/report.py",
    "engine/risk_gate.py",
    "engine/executor.py",
    "run_cycle.py",
    "config/risk_limits.yaml",
    "docs/STRATEGY.md",
)


def _git(repo: Path, *args: str, strip: bool = True) -> tuple[int, str]:
    """Run git and return (returncode, output).

    `strip` is opt-out because `git status --porcelain` encodes state in the
    first two columns. Stripping the output ate the leading space of " M path"
    and the fixed-offset parse then cut into the filename, so a dirty engine
    reported as clean. The helper was lossy for one caller and correct for the
    rest, which is the kind of thing that only shows up under test.
    """
    try:
        proc = subprocess.run(
            ("git", "-C", str(repo)) + args,
            capture_output=True, text=True, timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, str(exc)
    out = proc.stdout or proc.stderr
    return proc.returncode, out.strip() if strip else out


def describe_code(repo: str | Path, *, require_clean: bool = False) -> dict[str, Any]:
    """Derive the commit and verify it describes this engine.

    Raises ConfigError when the commit cannot be established, or when it does
    not contain the engine files. Refusing is the point: an unverifiable
    provenance record is worse than an absent one, because it reads as
    evidence.

    `require_clean` additionally refuses when tracked engine files are dirty.
    Off by default so that development runs work, but the dirty state is
    always recorded, and a live run should set it.
    """
    repo = Path(repo).resolve()

    code, head = _git(repo, "rev-parse", "HEAD")
    if code != 0 or len(head) != 40:
        raise ConfigError(
            f"cannot establish the current commit in {repo}: {head}. "
            f"The ledger's provenance record would be unverifiable, so the "
            f"cycle refuses to start rather than pin a value nobody can check."
        )

    missing = [
        f for f in ENGINE_FILES
        if _git(repo, "cat-file", "-e", f"{head}:{f}")[0] != 0
    ]
    if missing:
        raise ConfigError(
            f"commit {head[:12]} does not contain {missing}. This is exactly "
            f"the error that put code_commit 5748462 -- a tree with no engine "
            f"-- into the first ledger's genesis record. Commit the engine "
            f"before starting a ledger that claims to be produced by it."
        )

    code, porcelain = _git(repo, "status", "--porcelain", strip=False)
    dirty_all: list[str] = []
    if code == 0:
        for line in porcelain.splitlines():
            if not line.strip():
                continue
            # Columns 0-1 are the status code, 2 is a space, the rest is the
            # path. A rename reads "R  old -> new"; take the destination.
            path = line[3:].strip().strip('"')
            if " -> " in path:
                path = path.split(" -> ", 1)[1]
            dirty_all.append(path)
    dirty_engine = sorted(
        p for p in dirty_all
        if p in ENGINE_FILES or p.startswith("engine/")
    )

    if require_clean and dirty_engine:
        raise ConfigError(
            f"engine files are modified but uncommitted: {dirty_engine}. The "
            f"commit hash would verify while describing different code than "
            f"the code that runs. Commit or stash before a live run."
        )

    code, committed_at = _git(repo, "show", "-s", "--format=%cI", head)
    code, subject = _git(repo, "show", "-s", "--format=%s", head)

    return {
        "code_commit": head,
        "code_commit_short": head[:12],
        "committed_at": committed_at if code == 0 else None,
        "commit_subject": subject if code == 0 else None,
        "engine_files_verified_at_commit": list(ENGINE_FILES),
        "working_tree_clean": not dirty_all,
        "engine_files_dirty": dirty_engine,
        # Stated in the record itself, so a reader does not have to infer what
        # the pin does and does not establish.
        "pin_establishes": (
            "the listed files exist at this commit"
            + ("" if not dirty_engine else
               "; NOTE engine files are modified on disk, so the running code "
               "differs from this commit")
        ),
    }


def last_pinned_commit(records: Iterable[Mapping[str, Any]]) -> str | None:
    """The commit pinned by the chain's most recent decision.

    Cycle records carry their own `code_commit`, so a chain that spans engine
    changes has no single pin -- which is the point of `plan_code_change`:
    the change is recorded rather than left for an auditor to find by
    diffing commits. Older ledgers predate the field on cycle records, so
    any record that carries a pin is accepted as a fallback.
    """
    ordered = list(records)
    for rec in reversed(ordered):
        if rec.get("stage") in ("cycle", "code_change") and rec.get("code_commit"):
            return str(rec["code_commit"])
    for rec in reversed(ordered):
        if rec.get("code_commit"):
            return str(rec["code_commit"])
    return None


def engine_file_set_hash(repo: str | Path, commit: str) -> str | None:
    """Digest of which ENGINE_FILES exist at `commit`.

    Deliberately the file SET, not the contents: contents are what the commit
    hash already covers. The set answers the different question of whether
    the engine gained or lost a module between two pins.
    """
    code, listing = _git(Path(repo), "ls-tree", "-r", "--name-only", commit)
    if code != 0:
        return None
    present = set(listing.splitlines())
    kept = [f for f in ENGINE_FILES if f in present]
    return hashlib.sha256("\n".join(kept).encode("utf-8")).hexdigest()


def plan_code_change(
    records: Iterable[Mapping[str, Any]],
    *,
    current_commit: str,
    current_file_set_hash: str | None,
    previous_file_set_hash: str | None = None,
) -> dict[str, Any] | None:
    """A `code_change` record when the pin moved, otherwise None.

    The returned record names both commits and both file-set hashes, and
    carries `code_commit` for the new pin so that appending it once makes the
    next cycle agree and the record is not repeated.
    """
    previous = last_pinned_commit(records)
    if not previous or previous == current_commit:
        return None
    return {
        "stage": "code_change",
        "verdict": "OBSERVED",
        "reason_code": "CODE_COMMIT_CHANGED",
        "reason_text": (
            f"engine commit changed mid-chain: {previous[:12]} -> "
            f"{current_commit[:12]}. Recorded inside the chain so the change "
            f"is visible without diffing commits."
        ),
        "code_commit": current_commit,
        "from": {"code_commit": previous,
                 "engine_file_set_hash": previous_file_set_hash},
        "to": {"code_commit": current_commit,
               "engine_file_set_hash": current_file_set_hash},
    }
