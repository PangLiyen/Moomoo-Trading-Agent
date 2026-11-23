"""Regression tests for the genesis pinning error, 2026-11-21.

The first ledger's genesis record pinned `code_commit: "5748462"`, a commit
containing no engine files at all. `--code-commit` was free text defaulting to
"unknown", so the field could not distinguish a commit that describes the
running engine from one that does not -- and a wrong value there reads exactly
like a right one.

These tests build real git repositories, because the defect was precisely that
the check was absent rather than wrong. A mock would have passed the old code.
"""

import subprocess
from pathlib import Path

import pytest

from engine.errors import ConfigError
from engine.provenance import ENGINE_FILES, describe_code


def _git(repo: Path, *args: str) -> None:
    subprocess.run(("git", "-C", str(repo)) + args, check=True,
                   capture_output=True, text=True)


def _repo(tmp_path: Path, *, files: tuple[str, ...]) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    for rel in files:
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(f"# {rel}\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "spec only")
    return repo


def test_a_commit_without_the_engine_is_refused(tmp_path):
    """The exact 5748462 situation: spec and config committed, engine absent."""
    repo = _repo(tmp_path, files=("docs/STRATEGY.md", "config/risk_limits.yaml"))
    with pytest.raises(ConfigError) as exc:
        describe_code(repo)
    assert "does not contain" in str(exc.value)
    assert "5748462" in str(exc.value), (
        "the error should name the incident it prevents, so the next reader "
        "understands why the check exists"
    )


def test_a_commit_with_the_engine_is_accepted(tmp_path):
    repo = _repo(tmp_path, files=ENGINE_FILES)
    info = describe_code(repo)
    assert len(info["code_commit"]) == 40
    assert info["working_tree_clean"] is True
    assert info["engine_files_dirty"] == []


def test_a_partial_engine_is_refused_and_names_what_is_missing(tmp_path):
    """A glob would pass here. The file list is explicit for this reason."""
    repo = _repo(tmp_path, files=ENGINE_FILES[:-2])
    with pytest.raises(ConfigError) as exc:
        describe_code(repo)
    for missing in ENGINE_FILES[-2:]:
        assert missing in str(exc.value)


def test_a_dirty_engine_is_recorded_even_when_permitted(tmp_path):
    """A correct commit still does not describe the running code if the files
    are modified on disk. The hash verifies; the code differs."""
    repo = _repo(tmp_path, files=ENGINE_FILES)
    (repo / "engine" / "sizing.py").write_text("# changed\n", encoding="utf-8")

    info = describe_code(repo)
    assert info["working_tree_clean"] is False
    assert "engine/sizing.py" in info["engine_files_dirty"]
    assert "differs from this commit" in info["pin_establishes"], (
        "the record must say so itself; a reader should not have to cross-"
        "reference two fields to learn the pin is misleading"
    )


def test_require_clean_refuses_a_dirty_engine(tmp_path):
    repo = _repo(tmp_path, files=ENGINE_FILES)
    (repo / "engine" / "risk_gate.py").write_text("# changed\n", encoding="utf-8")
    with pytest.raises(ConfigError, match="modified but uncommitted"):
        describe_code(repo, require_clean=True)


def test_a_non_repository_is_refused_rather_than_pinned_as_unknown(tmp_path):
    """The old default was the string "unknown", which was recorded as though
    it were provenance. Refusing is correct: an unverifiable record reads as
    evidence while establishing nothing."""
    plain = tmp_path / "notarepo"
    plain.mkdir()
    with pytest.raises(ConfigError, match="cannot establish the current commit"):
        describe_code(plain)


def test_the_record_states_what_the_pin_does_and_does_not_establish(tmp_path):
    repo = _repo(tmp_path, files=ENGINE_FILES)
    info = describe_code(repo)
    assert info["pin_establishes"].startswith("the listed files exist at this commit")


def test_engine_files_covers_every_module_in_the_package():
    """ENGINE_FILES must mean what the register says it means.

    Its first version omitted errors.py, provenance.py and run_cycle.py while
    the audit register claimed the check covered "every engine file" -- so a
    commit holding only the listed paths would have passed. This test fails
    when a module is added to engine/ and not added here, which is the only
    way the claim stays true as the package grows.
    """
    pkg = Path(__file__).resolve().parents[1] / "engine"
    on_disk = {f"engine/{p.name}" for p in pkg.glob("*.py")}
    listed = set(ENGINE_FILES)
    missing = sorted(on_disk - listed)
    assert not missing, (
        f"engine modules absent from ENGINE_FILES: {missing}. A commit "
        f"without them would pass the provenance check while the register "
        f"claims every engine file is verified."
    )
