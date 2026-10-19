"""Tests for the Waterlevel.ie config flow."""

from __future__ import annotations

import aiohttp
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.waterlevel_ie.config_flow import normalize_station
from custom_components.waterlevel_ie.const import DOMAIN, GEOJSON_URL

pytestmark = pytest.mark.usefixtures("mock_setup_entry")

GROUP_LIST_HTML = """
<a href="/group/7/">River Barrow - Water Levels</a>
<a href="/group/3/">River Shannon</a>
"""
GROUP_7_HTML = """
<a href="/0000014029/">Graiguenamanagh</a>
<a href="/0000014019/">Levitstown</a>
"""
STATION_PAGE_HTML = """
<h1>Sensors on Station 14029 Graiguenamanagh Upper</h1>
<a href="/0000014029/0001/week/">Level</a>
<a href="/0000014029/0002/day/">Temp</a>
"""
SUMMARY_CSV = "Datetime,Value,Min,Mean,Max\n2026-10-18,,1.1,1.2,1.3\n"
GEOJSON = {
    "features": [
        {"properties": {"ref": "0000014029"}, "geometry": {"coordinates": [-6.95, 52.54]}},
    ]
}


def _mock_station(aioclient_mock: AiohttpClientMocker, station: str = "14029") -> None:
    padded = station.zfill(10)
    aioclient_mock.get(f"https://waterlevel.ie/{padded}/", text=STATION_PAGE_HTML)
    aioclient_mock.get(
        f"https://waterlevel.ie/{padded}/0001/summary/download/data.csv", text=SUMMARY_CSV
    )
    aioclient_mock.get(GEOJSON_URL, json=GEOJSON)
    # No flow data for this station
    aioclient_mock.get(
        f"https://waterlevel.ie/hydro-data/data/internet/stations/0/{station}/Q/week.json",
        status=404,
    )


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("14029", "14029"),
        (" 0000014029 ", "14029"),
        ("1234", "01234"),
        ("https://waterlevel.ie/0000014029/", "14029"),
        ("https://waterlevel.ie/0000014029/0001/", "14029"),
        ("abc", None),
        ("0", None),
        ("", None),
    ],
)
def test_normalize_station(raw: str, expected: str | None) -> None:
    assert normalize_station(raw) == expected


async def test_menu(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["nearby", "river", "station_number"]


async def test_station_number_creates_entry(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    _mock_station(aioclient_mock)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "station_number"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "station_number"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"station": "https://waterlevel.ie/0000014029/"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Graiguenamanagh Upper"
    assert result["data"] == {
        "station": "14029",
        "station_name": "Graiguenamanagh Upper",
        "available_sensors": [
            "0001",
            "0002",
            "0001_daily_mean",
            "0001_daily_min",
            "0001_daily_max",
        ],
        "latitude": 52.54,
        "longitude": -6.95,
    }


async def test_station_number_invalid_then_valid(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    _mock_station(aioclient_mock)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "station_number"}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"station": "graig"})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "station_number"
    assert result["errors"] == {"station": "invalid_station"}
    assert result["description_placeholders"]["stations_url"]

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"station": "14029"})
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_station_number_not_found(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get("https://waterlevel.ie/0000099999/", status=404)
    for sensor in ("0001", "0011", "OD", "0002", "0003", "0014"):
        aioclient_mock.get(f"https://waterlevel.ie/data/day/99999_{sensor}.csv", status=404)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "station_number"}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"station": "99999"})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "station_number"
    assert result["errors"] == {"station": "station_not_found"}


async def test_station_number_already_configured(hass: HomeAssistant) -> None:
    MockConfigEntry(domain=DOMAIN, unique_id=f"{DOMAIN}_14029").add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "station_number"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"station": "0000014029"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_browse_by_river(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get("https://waterlevel.ie/group/list/", text=GROUP_LIST_HTML)
    aioclient_mock.get("https://waterlevel.ie/group/7/", text=GROUP_7_HTML)
    _mock_station(aioclient_mock)
    # Levitstown is already added, so it should not be offered
    MockConfigEntry(domain=DOMAIN, unique_id=f"{DOMAIN}_14019").add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "river"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "river"
    options = result["data_schema"].schema["group"].config["options"]
    assert options == [
        {"value": "7", "label": "River Barrow"},
        {"value": "3", "label": "River Shannon"},
    ]

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"group": "7"})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "select_station"
    assert result["description_placeholders"] == {"river_name": "River Barrow"}
    options = result["data_schema"].schema["station"].config["options"]
    assert options == [{"value": "14029", "label": "Graiguenamanagh (14029)"}]

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"station": "14029"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    # The river picker's name wins over the station page title
    assert result["title"] == "Graiguenamanagh"


async def test_river_list_unreachable_aborts(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get("https://waterlevel.ie/group/list/", exc=aiohttp.ClientError())
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "river"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "cannot_load_rivers"


async def test_river_stations_unreachable_shows_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get("https://waterlevel.ie/group/list/", text=GROUP_LIST_HTML)
    aioclient_mock.get("https://waterlevel.ie/group/7/", exc=TimeoutError())
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "river"}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"group": "7"})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "river"
    assert result["errors"] == {"base": "cannot_connect"}


async def test_river_with_no_new_stations(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get("https://waterlevel.ie/group/list/", text=GROUP_LIST_HTML)
    aioclient_mock.get("https://waterlevel.ie/group/3/", text="<p>nothing</p>")
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "river"}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"group": "3"})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"group": "no_stations"}


async def test_station_number_cannot_connect(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get("https://waterlevel.ie/0000014029/", exc=aiohttp.ClientError())
    for sensor in ("0001", "0011", "OD", "0002", "0003", "0014"):
        aioclient_mock.get(
            f"https://waterlevel.ie/data/day/14029_{sensor}.csv", exc=aiohttp.ClientError()
        )
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "station_number"}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"station": "14029"})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}


def _latest_station(station: str, name: str, lon: float, lat: float) -> dict:
    return {
        "properties": {
            "station_ref": station.zfill(10),
            "station_name": name,
            "sensor_ref": "0001",
            "datetime": "2026-10-19T18:15:00Z",
            "value": "0.5",
        },
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
    }


LATEST_STATIONS = {
    "features": [
        # About 2 km and 11 km from the test home location, and one far away in Donegal
        _latest_station("14029", "Graiguenamanagh", -6.95, 52.54),
        _latest_station("14019", "Levitstown", -6.95, 52.45),
        _latest_station("01041", "Sandy Mills", -7.575758, 54.838318),
    ]
}


async def _start_nearby(hass: HomeAssistant) -> dict:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    return await hass.config_entries.flow.async_configure(
        result["flow_id"], {"next_step_id": "nearby"}
    )


@pytest.mark.usefixtures("mock_setup_entry")
async def test_nearby_lists_closest_first(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    hass.config.latitude, hass.config.longitude = 52.55, -6.97
    aioclient_mock.get("https://waterlevel.ie/geojson/latest/", json=LATEST_STATIONS)
    _mock_station(aioclient_mock)
    # Already added, so not listed
    MockConfigEntry(domain=DOMAIN, unique_id=f"{DOMAIN}_01041").add_to_hass(hass)

    result = await _start_nearby(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "nearby"
    location = next(key for key in result["data_schema"].schema if key == "location")
    assert location.default() == {"latitude": 52.55, "longitude": -6.97, "radius": 15000}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"location": {"latitude": 52.55, "longitude": -6.97, "radius": 500000}},
    )
    assert result["step_id"] == "nearby_station"
    options = result["data_schema"].schema["station"].config["options"]
    assert [o["value"] for o in options] == ["14029", "14019"]
    assert options[0]["label"] == "Graiguenamanagh (14029), 1.8 km"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"station": "14029"})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Graiguenamanagh"


async def test_nearby_station_error_stays_on_step(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get("https://waterlevel.ie/geojson/latest/", json=LATEST_STATIONS)
    aioclient_mock.get("https://waterlevel.ie/0000014029/", exc=aiohttp.ClientError())
    for sensor in ("0001", "0011", "OD", "0002", "0003", "0014"):
        aioclient_mock.get(
            f"https://waterlevel.ie/data/day/14029_{sensor}.csv", exc=aiohttp.ClientError()
        )
    result = await _start_nearby(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"location": {"latitude": 52.55, "longitude": -6.97, "radius": 15000}},
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"station": "14029"})
    assert result["step_id"] == "nearby_station"
    assert result["errors"] == {"base": "cannot_connect"}


async def test_nearby_none_in_range(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get("https://waterlevel.ie/geojson/latest/", json=LATEST_STATIONS)
    result = await _start_nearby(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"location": {"latitude": 51.9, "longitude": -10.0, "radius": 5000}},
    )
    assert result["step_id"] == "nearby"
    assert result["errors"] == {"base": "no_nearby_stations"}
    # The form keeps the location that was searched
    location = next(key for key in result["data_schema"].schema if key == "location")
    assert location.default()["radius"] == 5000


async def test_nearby_cannot_connect(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get("https://waterlevel.ie/geojson/latest/", exc=aiohttp.ClientError())
    result = await _start_nearby(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {"location": {"latitude": 52.55, "longitude": -6.97, "radius": 15000}},
    )
    assert result["step_id"] == "nearby"
    assert result["errors"] == {"base": "cannot_connect"}
