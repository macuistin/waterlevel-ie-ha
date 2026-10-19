"""Tests for the river flood warning blueprint."""

from __future__ import annotations

from pathlib import Path
import shutil
from typing import Any

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.setup import async_setup_component
import pytest
from pytest_homeassistant_custom_component.common import async_mock_service

BLUEPRINT = Path(__file__).parent.parent / "blueprints/automation/waterlevel_ie/river_rising.yaml"


def _install(config_dir: str) -> None:
    dest = Path(config_dir, "blueprints/automation/waterlevel_ie")
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy(BLUEPRINT, dest / BLUEPRINT.name)


async def _setup(hass: HomeAssistant, inputs: dict[str, Any]) -> list[ServiceCall]:
    await hass.async_add_executor_job(_install, hass.config.config_dir)
    hass.states.async_set("sensor.station_flood_stage", "normal")
    hass.states.async_set("sensor.station_water_level_trend", "0.0")
    calls = async_mock_service(hass, "test", "notify")
    config = {"automation": {"use_blueprint": {"path": f"waterlevel_ie/{BLUEPRINT.name}"}}}
    config["automation"]["use_blueprint"]["input"] = {
        "flood_stage": "sensor.station_flood_stage",
        **inputs,
    }
    assert await async_setup_component(hass, "automation", config)
    await hass.async_block_till_done()
    return calls


@pytest.fixture
async def calls(hass: HomeAssistant) -> list[ServiceCall]:
    return await _setup(
        hass,
        {
            "minimum_stage": "alert",
            "trend": "sensor.station_water_level_trend",
            "rate": 0.05,
            "actions": [{"action": "test.notify", "data": {"message": "{{ reason }}"}}],
        },
    )


async def test_runs_when_stage_reaches_minimum(
    hass: HomeAssistant, calls: list[ServiceCall]
) -> None:
    hass.states.async_set("sensor.station_flood_stage", "watch")
    await hass.async_block_till_done()
    assert calls == []

    hass.states.async_set("sensor.station_flood_stage", "alert")
    await hass.async_block_till_done()
    assert [c.data["message"] for c in calls] == ["flood stage is now alert"]

    # Falling back doesn't warn
    hass.states.async_set("sensor.station_flood_stage", "watch")
    await hass.async_block_till_done()
    assert len(calls) == 1


async def test_ignores_unavailable(hass: HomeAssistant, calls: list[ServiceCall]) -> None:
    hass.states.async_set("sensor.station_flood_stage", "unavailable")
    await hass.async_block_till_done()
    hass.states.async_set("sensor.station_flood_stage", "serious")
    await hass.async_block_till_done()
    assert calls == []


async def test_runs_when_rising_fast(hass: HomeAssistant, calls: list[ServiceCall]) -> None:
    hass.states.async_set("sensor.station_water_level_trend", "0.08")
    await hass.async_block_till_done()
    assert [c.data["message"] for c in calls] == ["river rising at 0.08 m/h"]


async def test_trend_sensor_is_optional(hass: HomeAssistant) -> None:
    calls = await _setup(
        hass,
        {
            "actions": [
                {"action": "test.notify", "data": {"message": "{{ station }}: {{ reason }}"}}
            ]
        },
    )
    assert hass.states.async_entity_ids("automation")

    hass.states.async_set("sensor.station_flood_stage", "watch")
    await hass.async_block_till_done()
    assert len(calls) == 1
    assert calls[0].data["message"].endswith("flood stage is now watch")
