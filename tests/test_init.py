"""Tests for setting up a Waterlevel.ie station."""

from __future__ import annotations

import aiohttp
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.waterlevel_ie.const import DOMAIN, GEOJSON_URL, LATEST_GEOJSON_URL

DAY_CSV = "datetime,value\n2026-10-19T18:00:00Z,1.30\n2026-10-19T18:15:00Z,1.42\n"
WEEK_CSV = "datetime,value\n2026-10-13T00:00:00Z,0.90\n2026-10-19T18:15:00Z,1.42\n"
SUMMARY_CSV = "Datetime,Value,Min,Mean,Max\n2026-10-18,,1.1,1.2,1.3\n"
TEMP_DAY_CSV = "datetime,value\n2026-10-19T18:15:00Z,11.5\n"


def _feature(station: str, sensor: str, value: str, time: str) -> dict:
    return {
        "type": "Feature",
        "properties": {
            "station_ref": station.zfill(10),
            "sensor_ref": sensor,
            "datetime": time,
            "value": value,
            "err_code": 99,
        },
        "geometry": {"type": "Point", "coordinates": [-6.95, 52.54]},
    }


LATEST = {
    "type": "FeatureCollection",
    "features": [
        # Older than the week CSV's last row, so the CSV value wins for 0001
        _feature("14029", "0001", "1.40", "2026-10-19T18:00:00Z"),
        _feature("14029", "0002", "12.300", "2026-10-19T18:15:00Z"),
        _feature("14019", "0001", "0.75", "2026-10-19T18:15:00Z"),
    ],
}


HYDRO_URL = (
    "https://waterlevel.ie/hydro-data/data/internet/stations/0/{station}/{param}/{period}.json"
)


def _mock_station(aioclient_mock: AiohttpClientMocker, station: str = "14029") -> None:
    """Mock a station's waterlevel.ie files. It has no OPW Hydro-Data history.

    Register any Hydro-Data mocks a test needs before calling this; the first match wins.
    """
    for param in ("S", "Q"):
        for period in ("week", "year"):
            aioclient_mock.get(
                HYDRO_URL.format(station=station, param=param, period=period), status=404
            )
    aioclient_mock.get(f"https://waterlevel.ie/data/day/{station}_0001.csv", text=DAY_CSV)
    aioclient_mock.get(f"https://waterlevel.ie/data/week/{station}_0001.csv", text=WEEK_CSV)
    aioclient_mock.get(
        f"https://waterlevel.ie/{station.zfill(10)}/0001/summary/download/data.csv",
        text=SUMMARY_CSV,
    )


def _entry(
    hass: HomeAssistant,
    station: str = "14029",
    name: str = "Graiguenamanagh",
    sensors: list[str] | None = None,
    coords: bool = True,
) -> MockConfigEntry:
    data = {
        "station": station,
        "station_name": name,
        "available_sensors": sensors or ["0001", "0001_daily_mean"],
    }
    if coords:
        data.update(latitude=52.54, longitude=-6.95)
    entry = MockConfigEntry(domain=DOMAIN, unique_id=f"{DOMAIN}_{station}", data=data)
    entry.add_to_hass(hass)
    return entry


def _calls(aioclient_mock: AiohttpClientMocker, url: str) -> int:
    return sum(1 for call in aioclient_mock.mock_calls if str(call[1]) == url)


async def test_setup_and_coords_backfill(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    _mock_station(aioclient_mock)
    aioclient_mock.get(LATEST_GEOJSON_URL, json=LATEST)
    # Unpadded ref: still matched to the station
    aioclient_mock.get(
        GEOJSON_URL,
        json={
            "features": [
                {"properties": {"ref": "14029"}, "geometry": {"coordinates": [-6.95, 52.54]}}
            ]
        },
    )
    entry = _entry(hass, coords=False)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    level = hass.states.get("sensor.graiguenamanagh_water_level")
    assert level.state == "1.42"
    assert level.attributes["trend"] == "rising"
    assert level.attributes["week_min"] == 0.9
    assert level.attributes["last_reported"] == "2026-10-19T18:15:00+00:00"
    assert level.attributes["latitude"] == 52.54
    assert hass.states.get("sensor.graiguenamanagh_daily_mean_water_level").state == "1.2"
    assert hass.states.get("sensor.graiguenamanagh_flood_stage").state == "normal"
    # Water level comes from the week CSV; the day CSV isn't needed
    assert _calls(aioclient_mock, "https://waterlevel.ie/data/day/14029_0001.csv") == 0

    assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_other_sensors_come_from_latest_feed(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    _mock_station(aioclient_mock)
    aioclient_mock.get(LATEST_GEOJSON_URL, json=LATEST)
    entry = _entry(hass, sensors=["0001", "0002"])
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.graiguenamanagh_temperature").state == "12.3"
    assert _calls(aioclient_mock, "https://waterlevel.ie/data/day/14029_0002.csv") == 0


async def test_latest_feed_fetched_once_for_all_stations(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    _mock_station(aioclient_mock, "14029")
    _mock_station(aioclient_mock, "14019")
    aioclient_mock.get(LATEST_GEOJSON_URL, json=LATEST)
    entry = _entry(hass)
    _entry(hass, "14019", "Levitstown")
    # Setting up the integration sets up both entries
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # Same timestamp as the week CSV, so the feed value is kept
    assert hass.states.get("sensor.levitstown_water_level").state == "0.75"
    assert _calls(aioclient_mock, LATEST_GEOJSON_URL) == 1


@pytest.mark.usefixtures("entity_registry_enabled_by_default")
async def test_falls_back_to_csv_when_feed_fails(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    _mock_station(aioclient_mock)
    aioclient_mock.get(LATEST_GEOJSON_URL, exc=aiohttp.ClientError())
    aioclient_mock.get("https://waterlevel.ie/data/day/14029_0002.csv", text=TEMP_DAY_CSV)
    entry = _entry(hass, sensors=["0001", "0002"])
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.graiguenamanagh_water_level").state == "1.42"
    assert hass.states.get("sensor.graiguenamanagh_temperature").state == "11.5"
