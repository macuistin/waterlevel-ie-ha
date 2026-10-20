"""Checks that keep the integration's metadata files consistent with the code."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
import re

import pytest

ROOT = Path(__file__).parent.parent
PKG = ROOT / "custom_components" / "waterlevel_ie"
PLATFORMS = ["sensor", "binary_sensor", "number"]


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_english_translation_is_a_copy_of_strings() -> None:
    assert (PKG / "translations" / "en.json").read_bytes() == (PKG / "strings.json").read_bytes()


def _shape(node: object, path: str = "") -> dict[str, frozenset[str]]:
    """Map each string's key path to the {placeholders} it uses."""
    if isinstance(node, dict):
        out: dict[str, frozenset[str]] = {}
        for key, value in node.items():
            out |= _shape(value, f"{path}/{key}")
        return out
    return {path: frozenset(re.findall(r"\{(\w+)\}", str(node)))}


def test_other_translations_match_strings() -> None:
    strings = _shape(_load(PKG / "strings.json"))
    for path in (PKG / "translations").glob("*.json"):
        assert _shape(_load(path)) == strings, path.name


@pytest.mark.parametrize("platform", PLATFORMS)
def test_platforms_set_parallel_updates(platform: str) -> None:
    module = importlib.import_module(f"custom_components.waterlevel_ie.{platform}")
    assert hasattr(module, "PARALLEL_UPDATES")


def test_every_entity_has_a_name_and_icon() -> None:
    names = _load(PKG / "strings.json")["entity"]
    icons = _load(PKG / "icons.json")["entity"]
    for platform, entities in names.items():
        assert set(entities) == set(icons[platform]), platform


def test_ci_tests_the_minimum_home_assistant() -> None:
    floor = _load(ROOT / "hacs.json")["homeassistant"]
    workflow = (ROOT / ".github" / "workflows" / "tests.yml").read_text(encoding="utf-8")
    assert re.search(rf'home-assistant: "{re.escape(floor)}"', workflow)


def test_manifest_and_project_versions_match() -> None:
    manifest = _load(PKG / "manifest.json")["version"]
    project = re.search(r'^version = "(.+)"$', (ROOT / "pyproject.toml").read_text(), re.M)
    assert project and project.group(1) == manifest
