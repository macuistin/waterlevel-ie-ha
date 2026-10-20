"""scripts/set_version.py sets and checks the release version, and reads release notes."""

from __future__ import annotations

import importlib.util
from pathlib import Path
import shutil
import sys

import pytest

ROOT = Path(__file__).parent.parent
_SPEC = importlib.util.spec_from_file_location("set_version", ROOT / "scripts" / "set_version.py")
set_version = importlib.util.module_from_spec(_SPEC)
sys.modules["set_version"] = set_version
_SPEC.loader.exec_module(set_version)


@pytest.fixture
def tree(tmp_path: Path) -> Path:
    """A copy of the files that state the version."""
    for place in set_version.PLACES:
        (tmp_path / place.path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(ROOT / place.path, tmp_path / place.path)
    return tmp_path


def test_the_repository_agrees_with_itself() -> None:
    assert set_version.mismatches(None) == []
    version = set_version.read_versions()["manifest.json"]
    # Every release has notes
    assert set_version.release_notes(version)


@pytest.mark.parametrize(("given", "version"), [("1.2.0", "1.2.0"), ("v1.3.0b1", "1.3.0b1")])
def test_normalise(given: str, version: str) -> None:
    assert set_version.normalise(given) == version


@pytest.mark.parametrize("given", ["1.2", "v1.2.0-beta", "latest", ""])
def test_normalise_rejects(given: str) -> None:
    with pytest.raises(set_version.VersionError):
        set_version.normalise(given)


def test_write_then_check(tree: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert set_version.main(["9.9.9"], tree) == 0
    versions = set_version.read_versions(tree)
    assert versions["manifest.json"] == versions["pyproject.toml"] == "9.9.9"
    # The changelog heading is renamed by hand, so the check fails until it is
    assert set_version.main(["--check", "v9.9.9"], tree) == 1
    assert "CHANGELOG.md" in capsys.readouterr().err

    changelog = tree / "CHANGELOG.md"
    changelog.write_text(
        changelog.read_text().replace("## v", "## v9.9.9\n\nNotes.\n\n## v", 1), encoding="utf-8"
    )
    assert set_version.main(["--check", "v9.9.9"], tree) == 0
    assert set_version.main(["--check"], tree) == 0
    assert set_version.main(["--notes", "v9.9.9"], tree) == 0
    assert capsys.readouterr().out.strip().endswith("Notes.")


def test_missing_places(tree: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tree / "pyproject.toml").write_text("[project]\n", encoding="utf-8")
    assert set_version.main(["9.9.9"], tree) == 1
    assert set_version.read_versions(tree)["manifest.json"] != "9.9.9"
    assert set_version.main(["--check"], tree) == 1
    assert set_version.main(["--notes", "v0.0.1"], tree) == 1
    assert "no section" in capsys.readouterr().err


def test_bad_arguments(tree: Path) -> None:
    assert set_version.main(["latest"], tree) == 2
    with pytest.raises(SystemExit):
        set_version.main([], tree)


def test_release_workflow_checks_the_version() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "set_version.py --check" in workflow
    assert "set_version.py --notes" in workflow


def test_release_workflow_can_be_run_by_hand() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    assert "workflow_dispatch:" in workflow
    # The typed version reaches the shell through env, never interpolated into a script
    assert "VERSION: ${{ inputs.version }}" in workflow
    assert workflow.count("${{ inputs.version }}") == 1
    assert "refs/heads/main" in workflow


def test_every_pre_release_version_publishes_a_pre_release() -> None:
    workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
    # set_version.py accepts a, b and rc versions; each must be marked a pre-release
    assert "(?:a|b|rc)" in set_version.VERSION
    assert '=~ (a|b|rc)[0-9]+$ ]]; then prerelease="--prerelease"' in workflow
