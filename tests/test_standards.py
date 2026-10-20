"""Checks that keep the Home Assistant quality scale tiers and the HACS standard met.

CLAUDE.md lists the rules for each standard; the ones a machine can check are checked here.
"""

from __future__ import annotations

import importlib
import inspect
import json
from pathlib import Path
import re
import tomllib

from homeassistant.helpers.entity import Entity
import pytest
import yaml

from custom_components.waterlevel_ie.entity import WaterlevelEntity

ROOT = Path(__file__).parent.parent
PKG = ROOT / "custom_components" / "waterlevel_ie"
WORKFLOWS = ROOT / ".github" / "workflows"
PLATFORMS = ["sensor", "binary_sensor", "number"]

# Every rule of the Home Assistant quality scale, by tier
# https://developers.home-assistant.io/docs/core/integration-quality-scale/rules
TIERS: dict[str, set[str]] = {
    "bronze": {
        "action-setup",
        "appropriate-polling",
        "brands",
        "common-modules",
        "config-flow-test-coverage",
        "config-flow",
        "dependency-transparency",
        "docs-actions",
        "docs-high-level-description",
        "docs-installation-instructions",
        "docs-removal-instructions",
        "entity-event-setup",
        "entity-unique-id",
        "has-entity-name",
        "runtime-data",
        "test-before-configure",
        "test-before-setup",
        "unique-config-entry",
    },
    "silver": {
        "action-exceptions",
        "config-entry-unloading",
        "docs-configuration-parameters",
        "docs-installation-parameters",
        "entity-unavailable",
        "integration-owner",
        "log-when-unavailable",
        "parallel-updates",
        "reauthentication-flow",
        "test-coverage",
    },
    "gold": {
        "devices",
        "diagnostics",
        "discovery-update-info",
        "discovery",
        "docs-data-update",
        "docs-examples",
        "docs-known-limitations",
        "docs-supported-devices",
        "docs-supported-functions",
        "docs-troubleshooting",
        "docs-use-cases",
        "dynamic-devices",
        "entity-category",
        "entity-device-class",
        "entity-disabled-by-default",
        "entity-translations",
        "exception-translations",
        "icon-translations",
        "reconfiguration-flow",
        "repair-issues",
        "stale-devices",
    },
    "platinum": {"async-dependency", "inject-websession", "strict-typing"},
}


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _rules() -> dict[str, dict]:
    raw = yaml.safe_load((PKG / "quality_scale.yaml").read_text(encoding="utf-8"))["rules"]
    return {name: r if isinstance(r, dict) else {"status": r} for name, r in raw.items()}


def _held_tier() -> str:
    match = re.search(r"holds the (\w+) tier", (ROOT / "CLAUDE.md").read_text(encoding="utf-8"))
    assert match, "CLAUDE.md must say which quality scale tier the repo holds"
    return match.group(1).lower()


def _readme_headings() -> set[str]:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    return {m.group(1).strip() for m in re.finditer(r"^#+ (.+)$", readme, re.M)}


# ── Every tier ────────────────────────────────────────────────────────────────


def test_quality_scale_lists_every_rule() -> None:
    assert set(_rules()) == set().union(*TIERS.values())


def test_held_tiers_have_no_todo() -> None:
    tiers = list(TIERS)
    held = tiers[: tiers.index(_held_tier()) + 1]
    rules = _rules()
    for tier in held:
        for rule in TIERS[tier]:
            assert rules[rule]["status"] in {"done", "exempt"}, f"{tier}: {rule}"


def test_exempt_rules_say_why() -> None:
    for name, rule in _rules().items():
        if rule["status"] == "exempt":
            assert rule.get("comment"), name


# ── Bronze ────────────────────────────────────────────────────────────────────


def test_docs_rules_name_a_readme_heading() -> None:
    text = (PKG / "quality_scale.yaml").read_text(encoding="utf-8")
    headings = _readme_headings()
    for name, rule in _rules().items():
        if not name.startswith("docs-") or rule["status"] != "done":
            continue
        match = re.search(rf"^\s*{name}: done\s+# README: ([^(\n]+)", text, re.M)
        assert match, f"{name} must name its README heading"
        assert match.group(1).strip() in headings, name


@pytest.mark.parametrize("flow", ["config", "options"])
def test_every_flow_field_has_a_description(flow: str) -> None:
    for step_id, step in _load(PKG / "strings.json")[flow]["step"].items():
        assert set(step.get("data", {})) == set(step.get("data_description", {})), step_id


def test_every_config_flow_step_is_tested() -> None:
    tests = (ROOT / "tests" / "test_config_flow.py").read_text(encoding="utf-8")
    for step_id in _load(PKG / "strings.json")["config"]["step"]:
        # Every flow starts at the user step
        if step_id != "user":
            assert f'"{step_id}"' in tests, step_id


@pytest.mark.parametrize("platform", PLATFORMS)
def test_entities_use_the_shared_base(platform: str) -> None:
    # Home Assistant turns _attr_ class attributes into properties, so read the source
    assert "_attr_has_entity_name = True" in inspect.getsource(WaterlevelEntity)
    module = importlib.import_module(f"custom_components.waterlevel_ie.{platform}")
    entities = [
        cls
        for _, cls in inspect.getmembers(module, inspect.isclass)
        if cls.__module__ == module.__name__ and issubclass(cls, Entity)
    ]
    assert entities
    for cls in entities:
        assert issubclass(cls, WaterlevelEntity), cls.__name__


def test_requirements_are_pinned_pypi_packages() -> None:
    for requirement in _load(PKG / "manifest.json")["requirements"]:
        assert re.fullmatch(r"[A-Za-z0-9._-]+==[\w.]+", requirement), requirement


def test_brand_assets_exist() -> None:
    for name in ["icon.png", "icon@2x.png", "logo.png", "logo@2x.png"]:
        assert (PKG / "brand" / name).is_file(), name


# ── Silver ────────────────────────────────────────────────────────────────────


def test_readme_documents_every_setup_field_and_option() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    strings = _load(PKG / "strings.json")
    steps = [*strings["config"]["step"].values(), *strings["options"]["step"].values()]
    for step in steps:
        for label in step.get("data", {}).values():
            assert f"| {label} |" in readme, label


def test_integration_has_an_owner() -> None:
    assert _load(PKG / "manifest.json")["codeowners"]


# ── Gold ──────────────────────────────────────────────────────────────────────


def test_raised_exceptions_are_translated() -> None:
    code = "\n".join(path.read_text(encoding="utf-8") for path in PKG.glob("*.py"))
    raised = set(re.findall(r'translation_domain=DOMAIN,\s*translation_key="(\w+)"', code))
    assert raised == set(_load(PKG / "strings.json")["exceptions"])


# ── Platinum ──────────────────────────────────────────────────────────────────


def test_mypy_is_strict_and_runs_in_ci() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["tool"]["mypy"]["strict"] is True
    assert re.search(r"^\s+- run: mypy$", (WORKFLOWS / "tests.yml").read_text(), re.M)


def test_type_ignores_name_their_error() -> None:
    for path in PKG.glob("*.py"):
        for line in path.read_text(encoding="utf-8").splitlines():
            assert not re.search(r"type: ignore(?!\[)", line), f"{path.name}: {line.strip()}"


def test_no_dependency_while_async_dependency_is_exempt() -> None:
    if _rules()["async-dependency"]["status"] == "exempt":
        assert _load(PKG / "manifest.json")["requirements"] == []


# ── HACS ──────────────────────────────────────────────────────────────────────


def test_hacs_manifest_keys() -> None:
    manifest = _load(PKG / "manifest.json")
    for key in ["domain", "name", "documentation", "issue_tracker", "codeowners", "version"]:
        assert manifest.get(key), key
    hacs = _load(ROOT / "hacs.json")
    assert hacs["name"] == manifest["name"]
    assert hacs["homeassistant"]


def test_one_integration_in_the_repo() -> None:
    dirs = [p.name for p in (ROOT / "custom_components").iterdir() if p.is_dir()]
    assert [d for d in dirs if d != "__pycache__"] == ["waterlevel_ie"]


@pytest.mark.parametrize("workflow", ["hacs.yml", "hassfest.yml", "tests.yml"])
def test_validation_runs_on_every_pull_request(workflow: str) -> None:
    on = yaml.safe_load((WORKFLOWS / workflow).read_text(encoding="utf-8"))[True]
    assert "pull_request" in on
