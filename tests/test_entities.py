"""Tests for the station's entities, availability and diagnostics."""

from __future__ import annotations

from datetime import timedelta

import aiohttp
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN, EntityCategory
from homeassistant.core import HomeAssistant, State
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    mock_restore_cache_with_extra_data,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.waterlevel_ie.const import DOMAIN, GEOJSON_URL, LATEST_GEOJSON_URL
from custom_components.waterlevel_ie.diagnostics import async_get_config_entry_diagnostics

STATION = "14029"
DATA_URL = "https://waterlevel.ie/data/{period}/14029_{sensor}.csv"
SUMMARY_URL = "https://waterlevel.ie/0000014029/0001/summary/download/data.csv"
SUMMARY_CSV = "Datetime,Value,Min,Mean,Max\n2026-10-18,,1.1,1.2,1.3\n"
GEOJSON = {
    "features": [{"properties": {"ref": "14029"}, "geometry": {"coordinates": [-6.95, 52.54]}}]
}
SENSORS = ["0001", "0002", "0003", "0014", "0001_daily_mean"]


def _csv(value: str, time: str = "2026-10-19T18:00:00Z") -> str:
    return f"datetime,value\n{time},{value}\n"


def _mock_readings(
    aioclient_mock: AiohttpClientMocker,
    level: str = "1.42",
    *,
    level_fails: bool = False,
    time: str = "2026-10-19T18:00:00Z",
) -> None:
    aioclient_mock.clear_requests()
    readings = {"0001": level, "0002": "11.5", "0003": "12.9", "0014": "-71"}
    for sensor, value in readings.items():
        for period in ("day", "week"):
            url = DATA_URL.format(period=period, sensor=sensor)
            if sensor == "0001" and level_fails:
                aioclient_mock.get(url, exc=aiohttp.ClientError("boom"))
            else:
                aioclient_mock.get(url, text=_csv(value, time))
    aioclient_mock.get(SUMMARY_URL, text=SUMMARY_CSV)
    aioclient_mock.get(GEOJSON_URL, json=GEOJSON)
    # Nothing in the shared feed, so every reading comes from the CSVs
    aioclient_mock.get(LATEST_GEOJSON_URL, json={"features": []})


def _mock_outage(aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.clear_requests()
    for sensor in ("0001", "0002", "0003", "0014"):
        for period in ("day", "week"):
            aioclient_mock.get(
                DATA_URL.format(period=period, sensor=sensor), exc=aiohttp.ClientError("down")
            )
    aioclient_mock.get(SUMMARY_URL, exc=aiohttp.ClientError("down"))
    aioclient_mock.get(GEOJSON_URL, exc=aiohttp.ClientError("down"))
    aioclient_mock.get(LATEST_GEOJSON_URL, exc=aiohttp.ClientError("down"))


async def _setup(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{DOMAIN}_{STATION}",
        title="Graiguenamanagh",
        data={
            "station": STATION,
            "station_name": "Graiguenamanagh",
            "available_sensors": SENSORS,
            "latitude": 52.54,
            "longitude": -6.95,
        },
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_entities_and_names(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, entity_registry: er.EntityRegistry
) -> None:
    _mock_readings(aioclient_mock)
    await _setup(hass)

    # Names come from strings.json and match the names used before translations,
    # so new installs get the same entity ids as before
    for entity_id in (
        "sensor.graiguenamanagh_water_level",
        "sensor.graiguenamanagh_temperature",
        "sensor.graiguenamanagh_daily_mean_water_level",
        "sensor.graiguenamanagh_flood_stage",
        "binary_sensor.graiguenamanagh_flood_alert",
        "number.graiguenamanagh_watch_level",
        "number.graiguenamanagh_alert_level",
        "number.graiguenamanagh_serious_level",
    ):
        assert hass.states.get(entity_id) is not None, entity_id

    level = hass.states.get("sensor.graiguenamanagh_water_level")
    assert level.state == "1.42"
    assert level.attributes["device_class"] == "distance"
    assert level.attributes["unit_of_measurement"] == "m"
    assert level.attributes["latitude"] == 52.54

    stage = hass.states.get("sensor.graiguenamanagh_flood_stage")
    assert stage.state == "normal"
    assert stage.attributes["options"] == ["normal", "watch", "alert", "serious"]
    assert stage.attributes["watch_level_m"] == 1.5
    assert hass.states.get("binary_sensor.graiguenamanagh_flood_alert").state == "off"

    # Diagnostic gauge readings are kept but off by default
    for unique_suffix in ("0003", "0014"):
        entity_id = entity_registry.async_get_entity_id(
            "sensor", DOMAIN, f"{DOMAIN}_14029_{unique_suffix}"
        )
        reg = entity_registry.async_get(entity_id)
        assert reg.entity_category is EntityCategory.DIAGNOSTIC
        assert reg.disabled_by is er.RegistryEntryDisabler.INTEGRATION

    watch = entity_registry.async_get("number.graiguenamanagh_watch_level")
    assert watch.entity_category is EntityCategory.CONFIG
    assert watch.unique_id == f"{DOMAIN}_14029_watch_threshold"


async def test_threshold_change_needs_no_request(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    _mock_readings(aioclient_mock, level="2.60")
    await _setup(hass)
    assert hass.states.get("sensor.graiguenamanagh_flood_stage").state == "alert"
    calls = aioclient_mock.call_count

    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": "number.graiguenamanagh_serious_level", "value": 2.5},
        blocking=True,
    )
    assert hass.states.get("number.graiguenamanagh_serious_level").state == "2.5"
    assert hass.states.get("sensor.graiguenamanagh_flood_stage").state == "serious"
    assert hass.states.get("binary_sensor.graiguenamanagh_flood_alert").state == "on"
    assert aioclient_mock.call_count == calls


async def test_short_outage_keeps_last_readings(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    _mock_readings(aioclient_mock)
    entry = await _setup(hass)

    _mock_outage(aioclient_mock)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    # Within the stale limit the last good readings stay, with their own timestamps
    level = hass.states.get("sensor.graiguenamanagh_water_level")
    assert level.state == "1.42"
    assert level.attributes["last_reported"] == "2026-10-19T18:00:00+00:00"
    assert hass.states.get("sensor.graiguenamanagh_flood_stage").state == "normal"
    assert hass.states.get("binary_sensor.graiguenamanagh_data_stale").state == "off"


async def test_long_outage_makes_entities_unavailable(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, frozen_now
) -> None:
    _mock_readings(aioclient_mock)
    entry = await _setup(hass)

    _mock_outage(aioclient_mock)
    frozen_now.tick(timedelta(hours=4))
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()

    # An outage must not read as "all clear"
    for entity_id in (
        "sensor.graiguenamanagh_water_level",
        "sensor.graiguenamanagh_flood_stage",
        "binary_sensor.graiguenamanagh_flood_alert",
    ):
        assert hass.states.get(entity_id).state == STATE_UNAVAILABLE, entity_id
    # Thresholds are local settings and stay editable
    assert hass.states.get("number.graiguenamanagh_watch_level").state == "1.5"
    # The freshness entities say what's wrong
    assert hass.states.get("binary_sensor.graiguenamanagh_data_stale").state == "on"
    assert hass.states.get("sensor.graiguenamanagh_data_age").state == "270"
    message = str(entry.runtime_data.last_exception)
    assert "Could not read any recent data for station 14029" in message
    assert "no reading newer than 3 hours" in message

    _mock_readings(aioclient_mock, time="2026-10-19T22:15:00Z")
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert hass.states.get("sensor.graiguenamanagh_water_level").state == "1.42"
    assert hass.states.get("binary_sensor.graiguenamanagh_data_stale").state == "off"


async def test_missing_level_makes_flood_stage_unknown(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    _mock_readings(aioclient_mock, level_fails=True)
    await _setup(hass)

    assert hass.states.get("sensor.graiguenamanagh_temperature").state == "11.5"
    assert hass.states.get("sensor.graiguenamanagh_water_level").state == STATE_UNKNOWN
    assert hass.states.get("sensor.graiguenamanagh_flood_stage").state == STATE_UNKNOWN
    assert hass.states.get("binary_sensor.graiguenamanagh_flood_alert").state == STATE_UNKNOWN


async def test_setup_retries_when_station_unreachable(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    _mock_outage(aioclient_mock)
    entry = await _setup(hass)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_thresholds_restored_after_restart(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State("number.graiguenamanagh_watch_level", "1.0"),
                {
                    "native_value": 1.0,
                    "native_min_value": 0.0,
                    "native_max_value": 15.0,
                    "native_step": 0.01,
                    "native_unit_of_measurement": "m",
                },
            )
        ],
    )
    _mock_readings(aioclient_mock, level="1.2")
    await _setup(hass)
    assert hass.states.get("number.graiguenamanagh_watch_level").state == "1.0"
    assert hass.states.get("sensor.graiguenamanagh_flood_stage").state == "watch"


async def test_diagnostics(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    _mock_readings(aioclient_mock)
    entry = await _setup(hass)

    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert diag["entry"]["data"]["station"] == STATION
    assert diag["coordinator"]["last_update_success"] is True
    assert diag["coordinator"]["update_interval_seconds"] == 900
    assert diag["data"]["0001"] == 1.42
    assert diag["thresholds"] == {"watch": 1.5, "alert": 2.5, "serious": 3.5}
    assert diag["flood_stage"] == "normal"


async def test_unload(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    _mock_readings(aioclient_mock)
    entry = await _setup(hass)
    assert await hass.config_entries.async_unload(entry.entry_id)
    assert entry.state is ConfigEntryState.NOT_LOADED


async def test_station_without_water_level(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A temperature-only station gets no flood entities, and no coordinates when unknown."""
    aioclient_mock.get(DATA_URL.format(period="day", sensor="0002"), text=_csv("9.5"))
    aioclient_mock.get(GEOJSON_URL, json={"features": []})
    aioclient_mock.get(LATEST_GEOJSON_URL, json={"features": []})
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{DOMAIN}_{STATION}",
        data={"station": STATION, "station_name": "Graiguenamanagh", "available_sensors": ["0002"]},
    )
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    temperature = hass.states.get("sensor.graiguenamanagh_temperature")
    assert temperature.state == "9.5"
    assert "latitude" not in temperature.attributes
    # Data Stale applies to every station; Flood Alert needs a water level
    assert hass.states.async_entity_ids("binary_sensor") == [
        "binary_sensor.graiguenamanagh_data_stale"
    ]
    assert hass.states.async_entity_ids("number") == []
    assert hass.states.get("sensor.graiguenamanagh_flood_stage") is None
