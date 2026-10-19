"""Tests for reading and parsing station data in the coordinator."""

from __future__ import annotations

from unittest.mock import patch

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.waterlevel_ie.config_flow import _detect_station
from custom_components.waterlevel_ie.const import DOMAIN, GEOJSON_URL
from custom_components.waterlevel_ie.coordinator import (
    WaterlevelCoordinator,
    fetch_station_coords,
)

DAY = "https://waterlevel.ie/data/day/14029_0001.csv"
WEEK = "https://waterlevel.ie/data/week/14029_0001.csv"
SUMMARY = "https://waterlevel.ie/0000014029/0001/summary/download/data.csv"


def _coordinator(hass: HomeAssistant, sensors: list[str]) -> WaterlevelCoordinator:
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={"station": "14029", "station_name": "Graig", "available_sensors": sensors},
    )
    entry.add_to_hass(hass)
    return WaterlevelCoordinator(hass, entry)


@pytest.mark.parametrize(
    ("level", "trend"), [("1.00", "falling"), ("1.21", "steady"), ("1.50", "rising")]
)
async def test_trend_against_yesterday_mean(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, level: str, trend: str
) -> None:
    aioclient_mock.get(DAY, text=f"datetime,value\n2026-10-19T18:00:00Z,{level}\n")
    aioclient_mock.get(WEEK, text="datetime,value\nbad,row\n2026-10-13T00:00:00Z,0.90\n")
    aioclient_mock.get(
        SUMMARY, text="Datetime,Value,Min,Mean,Max\n2026-10-18,,1.1,1.2,1.3\n2026-10-19,,x,y,z\n"
    )
    aioclient_mock.get(GEOJSON_URL, status=500)
    coordinator = _coordinator(hass, ["0001", "0001_daily_mean"])
    data = await coordinator._async_update_data()

    assert data["0001_daily_mean"] == 1.2
    assert coordinator.sensor_attrs["0001"]["trend"] == trend
    assert coordinator.sensor_attrs["0001"]["week_min"] == 0.9
    assert coordinator.latitude is None


async def test_falls_back_to_week_when_today_is_empty(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(DAY, status=404)
    aioclient_mock.get(
        WEEK, text="datetime,value\n2026-10-17T00:00:00Z,0.80\n2026-10-18T09:00:00Z,0.85\n,\n"
    )
    aioclient_mock.get(SUMMARY, exc=aiohttp.ClientError("down"))
    aioclient_mock.get(GEOJSON_URL, exc=aiohttp.ClientError("down"))
    coordinator = _coordinator(hass, ["0001", "0001_daily_min"])
    data = await coordinator._async_update_data()

    assert data["0001"] == 0.85
    assert data["0001_daily_min"] is None
    attrs = coordinator.sensor_attrs["0001"]
    assert attrs["last_reported"] == "2026-10-18T09:00:00Z"
    assert (attrs["week_min"], attrs["week_max"]) == (0.8, 0.85)
    assert "trend" not in attrs


async def test_bad_summary_rows_are_skipped(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(DAY, text="datetime,value\n2026-10-19T18:00:00Z,1.0\n")
    aioclient_mock.get(WEEK, exc=aiohttp.ClientError("down"))
    aioclient_mock.get(SUMMARY, text="Datetime,Value,Min,Mean,Max\n2026-10-19,,a,b,c\n")
    aioclient_mock.get(GEOJSON_URL, json={"features": []})
    coordinator = _coordinator(hass, ["0001", "0001_daily_max"])
    data = await coordinator._async_update_data()

    assert data["0001"] == 1.0
    assert data["0001_daily_max"] is None
    assert "week_min" not in coordinator.sensor_attrs["0001"]


async def test_coords_lookup_survives_bad_geojson(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(GEOJSON_URL, json={"features": [{"properties": {"ref": "14029"}}]})
    assert await fetch_station_coords(async_get_clientsession(hass), "14029") is None


async def test_detect_station_probes_csvs_when_page_fails(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get("https://waterlevel.ie/0000014029/", exc=aiohttp.ClientError("down"))
    aioclient_mock.get(DAY, text="datetime,value\n2026-10-19T18:00:00Z,1.0\n")
    for sensor in ("0011", "OD", "0002", "0003"):
        aioclient_mock.get(f"https://waterlevel.ie/data/day/14029_{sensor}.csv", status=404)
    aioclient_mock.get(
        "https://waterlevel.ie/data/day/14029_0014.csv", exc=aiohttp.ClientError("down")
    )
    aioclient_mock.get(SUMMARY, exc=aiohttp.ClientError("down"))
    aioclient_mock.get(GEOJSON_URL, status=404)

    info = await _detect_station(async_get_clientsession(hass), "14029")
    assert info == {
        "station": "14029",
        "station_name": "Station 14029",
        "available_sensors": ["0001"],
    }


@pytest.mark.usefixtures("mock_setup_entry")
async def test_flow_reports_unexpected_errors(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "station_number"}
    )
    with patch(
        "custom_components.waterlevel_ie.config_flow._detect_station", side_effect=RuntimeError
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"station": "14029"}
        )
    assert result["errors"] == {"base": "unknown"}
