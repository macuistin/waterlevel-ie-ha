"""Tests for setting up a Waterlevel.ie station."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.waterlevel_ie.const import DOMAIN, GEOJSON_URL

DAY_CSV = "datetime,value\n2026-10-19T18:00:00Z,1.30\n2026-10-19T18:15:00Z,1.42\n"
WEEK_CSV = "datetime,value\n2026-10-13T00:00:00Z,0.90\n2026-10-19T18:15:00Z,1.42\n"
SUMMARY_CSV = "Datetime,Value,Min,Mean,Max\n2026-10-18,,1.1,1.2,1.3\n"


async def test_setup_and_coords_backfill(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get("https://waterlevel.ie/data/day/14029_0001.csv", text=DAY_CSV)
    aioclient_mock.get("https://waterlevel.ie/data/week/14029_0001.csv", text=WEEK_CSV)
    aioclient_mock.get(
        "https://waterlevel.ie/0000014029/0001/summary/download/data.csv", text=SUMMARY_CSV
    )
    # Unpadded ref: still matched to the station
    aioclient_mock.get(
        GEOJSON_URL,
        json={
            "features": [
                {"properties": {"ref": "14029"}, "geometry": {"coordinates": [-6.95, 52.54]}}
            ]
        },
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{DOMAIN}_14029",
        data={
            "station": "14029",
            "station_name": "Graiguenamanagh",
            "available_sensors": ["0001", "0001_daily_mean"],
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    level = hass.states.get("sensor.graiguenamanagh_water_level")
    assert level.state == "1.42"
    assert level.attributes["trend"] == "rising"
    assert level.attributes["week_min"] == 0.9
    assert level.attributes["latitude"] == 52.54
    assert hass.states.get("sensor.graiguenamanagh_daily_mean_water_level").state == "1.2"
    assert hass.states.get("sensor.graiguenamanagh_flood_stage").state == "normal"

    assert await hass.config_entries.async_unload(entry.entry_id)
