"""Tests for the station details and alarm levels read from OPW Hydro-Data."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.waterlevel_ie.api import parse_alarm_levels, parse_station_info
from custom_components.waterlevel_ie.const import LATEST_GEOJSON_URL
from custom_components.waterlevel_ie.thresholds import alarm_thresholds

from .test_init import HYDRO_URL, LATEST, _entry, _mock_station
from .test_thresholds import HYDRO_WEEK, INDEX_URL, WEEK_CSV, WEEK_URL, YEAR_URL

ALARM_URL = HYDRO_URL.format(station="14029", param="S", period="alarmlevel")


def _series(name: str, value: float | None) -> dict:
    return {
        "ts_shortname": name,
        "ts_unitsymbol": "m",
        "data": [
            ["1973-01-01T00:00:00.000Z", 1.0],
            ["2025-01-01T00:00:00.000Z", value],
            ["2026-01-01T00:00:00.000Z", None],
        ],
    }


# Absolute levels: the live staff gauge reads 48 m lower
ALARM = [
    _series("WEB.Percentile-01", 49.5),
    _series("WEB.Percentile-05", 49.0),
    _series("WEB.YearHyd.Amax(median)", 50.5),
    _series("WEB.YearHyd.Amax(highest)", 51.25),
    _series("WEB.Percentile-99", 47.5),  # a low-flow level we don't use
    "not a series",
]
INDEX = {
    "station_no": "14029",
    "station_status": "Active",
    "catchment_name": "BARROW",
    "RBD": "SOUTH EASTERN RBD",
    "Stn_Class": " Level & Flow ",
    "CATCHMENT_SIZE": "113.00 km²",
    "ObjectDescription": "",
}


def test_parse_station_info() -> None:
    assert parse_station_info(INDEX) == {
        "status": "Active",
        "catchment": "BARROW",
        "river_basin_district": "SOUTH EASTERN RBD",
        "station_class": "Level & Flow",
        "catchment_area_km2": 113.0,
    }
    # Empty or odd values are left out
    assert parse_station_info({"CATCHMENT_SIZE": "n/a", "RBD": ""}) == {}
    assert parse_station_info([]) == {}


def test_parse_alarm_levels() -> None:
    assert parse_alarm_levels(ALARM) == {
        "p01": 49.5,
        "p05": 49.0,
        "annual_max_median": 50.5,
        "annual_max_highest": 51.25,
    }
    assert parse_alarm_levels({"not": "a list"}) == {}
    # A series with no values is skipped
    assert parse_alarm_levels([{"ts_shortname": "WEB.Percentile-01", "data": []}]) == {}


def _mock_info(aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get(INDEX_URL, json=INDEX)
    aioclient_mock.get(ALARM_URL, json=ALARM)
    aioclient_mock.get("https://waterlevel.ie/data/week/14029_0001.csv", text=WEEK_CSV)
    aioclient_mock.get(WEEK_URL, json=HYDRO_WEEK)
    aioclient_mock.get(YEAR_URL, status=404)
    _mock_station(aioclient_mock)
    aioclient_mock.get(LATEST_GEOJSON_URL, json={"features": []})


async def test_station_details_and_alarm_levels_are_shown(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    _mock_info(aioclient_mock)
    entry = _entry(hass, sensors=["0001"])
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data

    attrs = hass.states.get("sensor.graiguenamanagh_flood_stage").attributes
    assert attrs["catchment"] == "BARROW"
    assert attrs["catchment_area_km2"] == 113.0
    assert attrs["status"] == "Active"
    # Hydro-Data's levels, moved 48 m down onto the staff gauge
    assert attrs["median_annual_maximum_m"] == 2.5
    assert attrs["highest_annual_maximum_m"] == 3.25
    assert entry.data["station_info"]["levels"]["p01"] == 1.5
    # Storing them didn't reload the station
    assert entry.runtime_data is coordinator

    # A restart doesn't fetch them again
    calls = len(aioclient_mock.mock_calls)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert not any(str(c[1]) in (INDEX_URL, ALARM_URL) for c in aioclient_mock.mock_calls[calls:])
    attrs = hass.states.get("sensor.graiguenamanagh_flood_stage").attributes
    assert attrs["median_annual_maximum_m"] == 2.5


async def test_levels_are_left_out_without_a_datum_offset(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(INDEX_URL, json=INDEX)
    aioclient_mock.get(ALARM_URL, json=ALARM)
    _mock_station(aioclient_mock)  # no Hydro-Data readings to line the datums up with
    aioclient_mock.get(LATEST_GEOJSON_URL, json=LATEST)
    entry = _entry(hass, sensors=["0001"])
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    attrs = hass.states.get("sensor.graiguenamanagh_flood_stage").attributes
    assert attrs["catchment"] == "BARROW"
    assert "median_annual_maximum_m" not in attrs
    assert entry.data["station_info"]["levels"] == {}


async def test_station_without_details(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    _mock_station(aioclient_mock)
    aioclient_mock.get(LATEST_GEOJSON_URL, json=LATEST)
    entry = _entry(hass, sensors=["0001"])
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.data["station_info"] == {"details": {}, "levels": {}}
    assert "catchment" not in hass.states.get("sensor.graiguenamanagh_flood_stage").attributes


async def test_details_unreachable_are_retried_next_setup(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(INDEX_URL, status=500)
    _mock_station(aioclient_mock)
    aioclient_mock.get(LATEST_GEOJSON_URL, json=LATEST)
    entry = _entry(hass, sensors=["0001"])
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert "station_info" not in entry.data


def test_alarm_thresholds() -> None:
    levels = {"p01": 1.4, "annual_max_median": 1.47, "annual_max_highest": 2.52, "p05": 1.0}
    assert alarm_thresholds(levels) == {"watch": 1.4, "alert": 1.47, "serious": 2.52}
    # Out-of-order levels are put in order
    assert alarm_thresholds({"p01": 2.0, "annual_max_median": 1.0, "annual_max_highest": 1.5}) == {
        "watch": 2.0,
        "alert": 2.0,
        "serious": 2.0,
    }
    # A station missing any of the three gets none
    assert alarm_thresholds({"p01": 1.4, "annual_max_median": 1.47}) is None
    assert alarm_thresholds({}) is None


async def test_alarm_levels_set_the_default_thresholds(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    _mock_info(aioclient_mock)
    entry = _entry(hass, sensors=["0001"])
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    for stage, expected in (("watch", 1.5), ("alert", 2.5), ("serious", 3.25)):
        assert hass.states.get(f"number.graiguenamanagh_{stage}_level").state == str(expected)
    info = hass.states.get("sensor.graiguenamanagh_station_information")
    assert info.state == "alarm_levels"
    assert info.attributes["catchment"] == "BARROW"
    assert info.attributes["exceeded_1_percent_level_m"] == 1.5
    assert info.attributes["median_annual_maximum_m"] == 2.5
    assert info.attributes["highest_annual_maximum_m"] == 3.25
    assert info.attributes["default_watch_level_m"] == 1.5
    assert info.attributes["default_serious_level_m"] == 3.25
    assert info.attributes["default_alert_level_m"] == 2.5


async def test_a_threshold_the_user_set_beats_the_alarm_levels(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    _mock_info(aioclient_mock)
    entry = _entry(hass, sensors=["0001"])
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data

    coordinator.set_threshold("alert", 2.0)
    coordinator.station_info = None  # looked up again, as on a first setup
    await coordinator.async_load_station_info()
    assert coordinator.get_threshold("alert") == 2.0
    assert coordinator.get_threshold("watch") == 1.5


async def test_fallback_is_reported_without_alarm_levels(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    _mock_station(aioclient_mock)
    aioclient_mock.get(LATEST_GEOJSON_URL, json=LATEST)
    entry = _entry(hass, sensors=["0001"])
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    info = hass.states.get("sensor.graiguenamanagh_station_information")
    assert info.state == "fallback"
    assert info.attributes["default_watch_level_m"] == 1.5
    assert info.attributes["default_alert_level_m"] == 2.5
    assert info.attributes["default_serious_level_m"] == 3.5
    assert "median_annual_maximum_m" not in info.attributes
    assert entry.runtime_data.default_thresholds()[1] == "fallback"
