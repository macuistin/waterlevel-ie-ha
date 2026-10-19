"""Tests for the station options flow."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.waterlevel_ie.const import LATEST_GEOJSON_URL, SENSOR_TYPES

from .test_init import LATEST, _entry, _mock_station

STATION_PAGE_URL = "https://waterlevel.ie/0000014029/"
STATION_PAGE_HTML = """
<h1>Sensors on Station 14029 Graiguenamanagh</h1>
<a href="/0000014029/0001/week/">Level</a>
<a href="/0000014029/0002/day/">Temp</a>
"""


async def _setup(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> MockConfigEntry:
    _mock_station(aioclient_mock)
    aioclient_mock.get(LATEST_GEOJSON_URL, json=LATEST)
    entry = _entry(hass, sensors=["0001"])
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_defaults(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    entry = await _setup(hass, aioclient_mock)
    assert entry.runtime_data.update_interval == timedelta(minutes=15)
    assert entry.runtime_data.stale_after == timedelta(hours=3)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    schema = result["data_schema"].schema
    defaults = {str(key): key.default() for key in schema}
    assert defaults == {
        "scan_interval": 15,
        "stale_hours": 3,
        "alert_stage": "watch",
        "redetect_sensors": False,
    }


async def test_change_interval_and_stale_limit(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    entry = await _setup(hass, aioclient_mock)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"scan_interval": 30, "stale_hours": 6}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert entry.options == {"scan_interval": 30, "stale_hours": 6, "alert_stage": "watch"}
    await hass.async_block_till_done()

    # The station reloaded with the new settings
    assert entry.runtime_data.update_interval == timedelta(minutes=30)
    assert entry.runtime_data.stale_after == timedelta(hours=6)

    # The form now shows the saved values
    result = await hass.config_entries.options.async_init(entry.entry_id)
    defaults = {str(key): key.default() for key in result["data_schema"].schema}
    assert defaults["scan_interval"] == 30
    assert defaults["stale_hours"] == 6


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_redetect_adds_new_sensors(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    entry = await _setup(hass, aioclient_mock)
    assert hass.states.get("sensor.graiguenamanagh_temperature") is None
    aioclient_mock.get(STATION_PAGE_URL, text=STATION_PAGE_HTML)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    with patch.object(
        hass.config_entries, "async_reload", wraps=hass.config_entries.async_reload
    ) as reload:
        result = await hass.config_entries.options.async_configure(
            result["flow_id"], {"scan_interval": 15, "stale_hours": 3, "redetect_sensors": True}
        )
        await hass.async_block_till_done()
    assert result["type"] is FlowResultType.CREATE_ENTRY

    assert entry.data["available_sensors"] == [
        "0001",
        "0002",
        "0001_daily_mean",
        "0001_daily_min",
        "0001_daily_max",
    ]
    assert entry.options == {"scan_interval": 15, "stale_hours": 3, "alert_stage": "watch"}
    # New sensors and options are saved together, so the station reloads once
    assert reload.call_count == 1
    assert hass.states.get("sensor.graiguenamanagh_temperature").state == "12.3"
    assert hass.states.get("sensor.graiguenamanagh_daily_min_water_level") is not None


async def test_redetect_station_gone(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    entry = await _setup(hass, aioclient_mock)
    aioclient_mock.clear_requests()
    aioclient_mock.get(STATION_PAGE_URL, status=404)
    for sensor in SENSOR_TYPES:
        aioclient_mock.get(f"https://waterlevel.ie/data/day/14029_{sensor}.csv", status=404)

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"scan_interval": 15, "stale_hours": 3, "redetect_sensors": True}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "station_not_found"}
    assert entry.data["available_sensors"] == ["0001"]
    assert entry.options == {}


async def test_redetect_cannot_connect(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    entry = await _setup(hass, aioclient_mock)
    aioclient_mock.clear_requests()
    aioclient_mock.get(STATION_PAGE_URL, exc=aiohttp.ClientError())
    for sensor in SENSOR_TYPES:
        aioclient_mock.get(
            f"https://waterlevel.ie/data/day/14029_{sensor}.csv", exc=aiohttp.ClientError()
        )

    result = await hass.config_entries.options.async_init(entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"scan_interval": 15, "stale_hours": 3, "redetect_sensors": True}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}
    assert entry.data["available_sensors"] == ["0001"]
