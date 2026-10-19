"""Tests for keeping the last good reading and reporting how fresh the data is."""

from __future__ import annotations

from datetime import timedelta

import aiohttp
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.waterlevel_ie.const import LATEST_GEOJSON_URL

from .test_init import LATEST, _entry, _feature, _mock_station


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_keeps_last_reading_and_flags_stale(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer: FrozenDateTimeFactory
) -> None:
    freezer.move_to("2026-10-19T18:30:00Z")
    _mock_station(aioclient_mock)
    aioclient_mock.get(LATEST_GEOJSON_URL, json=LATEST)
    entry = _entry(hass, sensors=["0001", "0002"])
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert (
        hass.states.get("sensor.graiguenamanagh_last_reading").state == "2026-10-19T18:15:00+00:00"
    )
    assert hass.states.get("sensor.graiguenamanagh_data_age").state == "15"
    assert hass.states.get("binary_sensor.graiguenamanagh_data_stale").state == "off"

    # waterlevel.ie goes down for two hours
    aioclient_mock.clear_requests()
    aioclient_mock.get(LATEST_GEOJSON_URL, exc=aiohttp.ClientError())
    aioclient_mock.get("https://waterlevel.ie/data/week/14029_0001.csv", exc=aiohttp.ClientError())
    aioclient_mock.get("https://waterlevel.ie/data/day/14029_0002.csv", status=503)
    aioclient_mock.get("https://waterlevel.ie/data/week/14029_0002.csv", status=503)
    freezer.tick(timedelta(hours=2))
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    level = hass.states.get("sensor.graiguenamanagh_water_level")
    assert level.state == "1.42"
    assert level.attributes["last_reported"] == "2026-10-19T18:15:00+00:00"
    assert level.attributes["week_min"] == 0.9
    assert hass.states.get("sensor.graiguenamanagh_temperature").state == "12.3"
    assert hass.states.get("sensor.graiguenamanagh_data_age").state == "135"
    assert hass.states.get("binary_sensor.graiguenamanagh_data_stale").state == "off"


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_station_with_only_old_data_sets_up_as_stale(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, frozen_now: FrozenDateTimeFactory
) -> None:
    # A gauge that stopped reporting: its last reading is a day old
    _mock_station(aioclient_mock)
    aioclient_mock.get(
        LATEST_GEOJSON_URL,
        json={"features": [_feature("14029", "0002", "9.0", "2026-10-18T18:00:00Z")]},
    )
    entry = _entry(hass, sensors=["0002"])
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # It still sets up, so the freshness entities can say the gauge has gone quiet
    assert entry.state is ConfigEntryState.LOADED
    assert hass.states.get("sensor.graiguenamanagh_temperature").state == STATE_UNAVAILABLE
    assert hass.states.get("binary_sensor.graiguenamanagh_data_stale").state == "on"
    last = hass.states.get("sensor.graiguenamanagh_last_reading")
    assert last.state == "2026-10-18T18:00:00+00:00"

    # It keeps polling, and recovers when the gauge reports again
    coordinator = entry.runtime_data
    assert coordinator._unsub_refresh is not None
    aioclient_mock.clear_requests()
    _mock_station(aioclient_mock)
    aioclient_mock.get(
        LATEST_GEOJSON_URL,
        json={"features": [_feature("14029", "0002", "9.5", "2026-10-19T18:30:00Z")]},
    )
    frozen_now.tick(timedelta(minutes=15))
    await coordinator.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("sensor.graiguenamanagh_temperature").state == "9.5"
    assert hass.states.get("binary_sensor.graiguenamanagh_data_stale").state == "off"
