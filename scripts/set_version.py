#!/usr/bin/env python3
"""Set, or check, the release version in the places that state it.

The places are manifest.json, pyproject.toml and the newest release heading in
CHANGELOG.md. A version is three numbers with an optional beta or release candidate
suffix: 1.2.0, 1.2.0b1, 1.2.0rc2. A git tag is that version behind a "v" (v1.2.0).

Usage:
    python scripts/set_version.py 1.2.0            # write the version to manifest and pyproject
    python scripts/set_version.py --check v1.2.0   # exit 1 unless every place equals the tag
    python scripts/set_version.py --check          # exit 1 unless the places agree
    python scripts/set_version.py --notes v1.2.0   # print that version's changelog section

Rename the changelog's heading by hand: it is where the release notes are written.
Exit codes: 0 done, 1 a place differs or is missing, 2 the argument is not a version.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
VERSION = r"[0-9]+\.[0-9]+\.[0-9]+(?:(?:a|b|rc)[0-9]+)?"
_VERSION_ONLY = re.compile(VERSION)


@dataclass(frozen=True)
class Place:
    """One file that states the version, and the pattern that finds it.

    The pattern has two groups: the text before the version and the version itself.
    """

    name: str
    path: str
    pattern: re.Pattern[str]
    written: bool = True


PLACES: tuple[Place, ...] = (
    Place(
        "manifest.json",
        "custom_components/waterlevel_ie/manifest.json",
        re.compile(rf'(?m)^(\s*"version": ")({VERSION})(?=")'),
    ),
    Place("pyproject.toml", "pyproject.toml", re.compile(rf'(?m)^(version = ")({VERSION})(?=")')),
    # The first "## v..." heading is the newest release
    Place("CHANGELOG.md", "CHANGELOG.md", re.compile(rf"(?m)^(## v)({VERSION})$"), written=False),
)


class VersionError(Exception):
    """The argument is not a version or a tag."""


def normalise(value: str) -> str:
    """The version in a version or a tag: a leading "v" is dropped."""
    version = value.strip().removeprefix("v")
    if not _VERSION_ONLY.fullmatch(version):
        raise VersionError(f"{value!r} is not a version. Use 1.2.0, 1.2.0b1 or 1.2.0rc1.")
    return version


def read_versions(root: Path = ROOT) -> dict[str, str | None]:
    """The version each place states, or None where the pattern finds nothing."""
    found: dict[str, str | None] = {}
    for place in PLACES:
        match = place.pattern.search((root / place.path).read_text(encoding="utf-8"))
        found[place.name] = match.group(2) if match else None
    return found


def mismatches(expected: str | None, root: Path = ROOT) -> list[str]:
    """The places that don't state expected, or that don't agree when it is None."""
    versions = read_versions(root)
    absent = [f"{name}: no version found" for name, found in versions.items() if found is None]
    if absent:
        return absent
    target = expected if expected is not None else next(iter(versions.values()))
    return [
        f"{name}: {found}, expected {target}" for name, found in versions.items() if found != target
    ]


def write_version(version: str, root: Path = ROOT) -> list[str]:
    """Write version to manifest.json and pyproject.toml; return the places not found."""
    places = [place for place in PLACES if place.written]
    texts = {place: (root / place.path).read_text(encoding="utf-8") for place in places}
    missing = [place.name for place, text in texts.items() if not place.pattern.search(text)]
    if missing:
        return missing
    for place, text in texts.items():
        updated = place.pattern.sub(lambda m: f"{m.group(1)}{version}", text, count=1)
        (root / place.path).write_text(updated, encoding="utf-8")
    return []


def release_notes(version: str, root: Path = ROOT) -> str | None:
    """The CHANGELOG.md section for a version, without its heading, or None."""
    text = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    match = re.search(rf"(?ms)^## v{re.escape(version)}\n(.*?)(?=^## |\Z)", text)
    return match.group(1).strip() if match else None


def _report(problems: list[str]) -> int:
    for problem in problems:
        print(problem, file=sys.stderr)
    return 1 if problems else 0


def main(argv: list[str] | None = None, root: Path = ROOT) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("version", nargs="?", help="a version or a tag, for example v1.2.0")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="check instead of writing")
    mode.add_argument("--notes", action="store_true", help="print the version's changelog")
    args = parser.parse_args(argv)
    try:
        version = normalise(args.version) if args.version else None
    except VersionError as err:
        print(err, file=sys.stderr)
        return 2
    if args.check:
        status = _report(mismatches(version, root))
        if status == 0:
            print(f"All {len(PLACES)} places agree.")
        return status
    if version is None:
        parser.error("a version is required unless --check is given")
    if args.notes:
        if (notes := release_notes(version, root)) is None:
            return _report([f"CHANGELOG.md: no section for v{version}"])
        print(notes)
        return 0
    status = _report(
        [f"{name}: no version found, nothing was written" for name in write_version(version, root)]
    )
    if status == 0:
        print(f"Set {version}. Rename the CHANGELOG.md heading; the tag is v{version}.")
    return status


if __name__ == "__main__":
    sys.exit(main())
