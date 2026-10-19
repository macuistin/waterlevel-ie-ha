"""Tests for flood thresholds suggested from a station's history, and the alert stage."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from homeassistant.core import HomeAssistant, State
from pytest_homeassistant_custom_component.common import mock_restore_cache_with_extra_data
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.waterlevel_ie.api import Reading, parse_hydro_series
from custom_components.waterlevel_ie.const import LATEST_GEOJSON_URL
from custom_components.waterlevel_ie.thresholds import datum_offset, suggest_thresholds

from .test_init import HYDRO_URL, LATEST, _entry, _mock_station

START = datetime(2026, 10, 19, 17, 0, tzinfo=UTC)
# Staff gauge readings every 15 minutes; Hydro-Data has the same levels 48 m higher
STAFF = [(START + timedelta(minutes=15 * i), 1.0 + i / 100) for i in range(6)]
WEEK_CSV = "datetime,value\n" + "".join(
    f"{t.strftime('%Y-%m-%dT%H:%M:%SZ')},{v:.2f}\n" for t, v in STAFF
)
HYDRO_WEEK = [
    {
        "ts_shortname": "WEB.Cmd.P-Continuous.Absolute",
        "data": [[t.strftime("%Y-%m-%dT%H:%M:%S.000Z"), v + 48.0, 254] for t, v in STAFF],
    }
]
# 1000 days of daily means from 48.00 to 50.00 m (absolute)
HYDRO_YEAR = [
    {
        "ts_shortname": "WEB.Day.Mean",
        "data": [
            [
                (START - timedelta(days=1000 - i)).strftime("%Y-%m-%dT00:00:00.000Z"),
                48.0 + i / 500,
                31,
            ]
            for i in range(1001)
        ],
    }
]
WEEK_URL = HYDRO_URL.format(station="14029", param="S", period="week")
YEAR_URL = HYDRO_URL.format(station="14029", param="S", period="year")
WATCH_RESTORE = (
    State("number.graiguenamanagh_watch_level", "1.2"),
    {
        "native_value": 1.2,
        "native_min_value": 0.0,
        "native_max_value": 15.0,
        "native_step": 0.01,
        "native_unit_of_measurement": "m",
    },
)


def _readings(pairs) -> list[Reading]:
    return [Reading(t, v) for t, v in pairs]


def test_parse_hydro_series() -> None:
    payload = [
        "not a series",
        {"ts_shortname": "Other", "data": [["2026-10-19T00:00:00.000Z", 1.0]]},
        {
            "ts_shortname": "WEB.Day.Mean",
            "data": [
                ["2026-10-18T00:00:00.000Z", None, 31],
                ["2026-10-18T00:00:00.000Z", "x", 31],
                ["bad time", 1.0],
                ["2026-10-19T00:00:00.000Z"],
                ["2026-10-19T00:00:00.000Z", 49.5, 31],
            ],
        },
    ]
    assert parse_hydro_series(payload, "WEB.Day.Mean") == [
        Reading(datetime(2026, 10, 19, tzinfo=UTC), 49.5)
    ]
    # Without a name, the first series with data
    assert parse_hydro_series(payload)[0].value == 1.0
    assert parse_hydro_series({"not": "a list"}) == []


def test_datum_offset() -> None:
    staff = _readings(STAFF)
    absolute = _readings((t, v + 48.0) for t, v in STAFF)
    assert round(datum_offset(staff, absolute), 6) == -48.0
    # Too few matching times
    assert datum_offset(staff[:3], absolute) is None


def test_suggest_thresholds() -> None:
    days = _readings((START - timedelta(days=i), 48.0 + i / 500) for i in range(1001))
    assert suggest_thresholds(days, -48.0) == {
        "watch": 1.9,
        "alert": 1.98,
        "serious": 2.0,
        "typical": 1.0,
        "record": 2.0,
    }
    assert suggest_thresholds(days[:100], -48.0) is None
    # A flat record still gives stages in order
    flat = _readings((START - timedelta(days=i), 48.0) for i in range(400))
    assert suggest_thresholds(flat, -48.0)["serious"] == 0.0


def _mock_history(aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get("https://waterlevel.ie/data/week/14029_0001.csv", text=WEEK_CSV)
    aioclient_mock.get(WEEK_URL, json=HYDRO_WEEK)
    aioclient_mock.get(YEAR_URL, json=HYDRO_YEAR)
    _mock_station(aioclient_mock)
    aioclient_mock.get(LATEST_GEOJSON_URL, json={"features": []})


async def test_new_station_uses_suggested_thresholds(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    _mock_history(aioclient_mock)
    entry = _entry(hass, sensors=["0001"])
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    coordinator = entry.runtime_data

    assert entry.data["suggested_thresholds"]["watch"] == 1.9
    watch = hass.states.get("number.graiguenamanagh_watch_level")
    assert watch.state == "1.9"
    assert watch.attributes["suggested_value"] == 1.9
    assert hass.states.get("number.graiguenamanagh_serious_level").state == "2.0"
    stage = hass.states.get("sensor.graiguenamanagh_flood_stage")
    assert stage.attributes["typical_level_m"] == 1.0
    assert stage.attributes["record_daily_mean_m"] == 2.0
    assert stage.attributes["watch_level_m"] == 1.9
    # Storing the suggestions didn't reload the station
    assert entry.runtime_data is coordinator

    # A restart doesn't fetch the history again
    calls = len(aioclient_mock.mock_calls)
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert not any(str(c[1]) == YEAR_URL for c in aioclient_mock.mock_calls[calls:])
    assert hass.states.get("number.graiguenamanagh_watch_level").state == "1.9"


async def test_user_thresholds_are_kept(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_restore_cache_with_extra_data(hass, [WATCH_RESTORE])
    _mock_history(aioclient_mock)
    entry = _entry(hass, sensors=["0001"])
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    watch = hass.states.get("number.graiguenamanagh_watch_level")
    assert watch.state == "1.2"
    assert watch.attributes["suggested_value"] == 1.9
    # Thresholds the user never set still move to the suggestions
    assert hass.states.get("number.graiguenamanagh_alert_level").state == "1.98"


async def test_station_without_history_keeps_defaults(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    _mock_station(aioclient_mock)
    aioclient_mock.get(LATEST_GEOJSON_URL, json=LATEST)
    entry = _entry(hass, sensors=["0001"])
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.data["suggested_thresholds"] == {}
    watch = hass.states.get("number.graiguenamanagh_watch_level")
    assert watch.state == "1.5"
    assert "suggested_value" not in watch.attributes
    assert "typical_level_m" not in hass.states.get("sensor.graiguenamanagh_flood_stage").attributes


async def test_history_unreachable_is_retried_next_setup(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(YEAR_URL, status=500)
    _mock_station(aioclient_mock)
    aioclient_mock.get(LATEST_GEOJSON_URL, json=LATEST)
    entry = _entry(hass, sensors=["0001"])
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # Nothing stored, so the next setup looks again
    assert "suggested_thresholds" not in entry.data
    assert hass.states.get("number.graiguenamanagh_watch_level").state == "1.5"


async def test_station_without_water_level_skips_history(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get("https://waterlevel.ie/data/day/14029_0002.csv", text="")
    aioclient_mock.get(LATEST_GEOJSON_URL, json=LATEST)
    entry = _entry(hass, sensors=["0002"])
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert "suggested_thresholds" not in entry.data
    assert not any("hydro-data" in str(c[1]) for c in aioclient_mock.mock_calls)


async def test_alert_stage_option(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    _mock_station(aioclient_mock)
    aioclient_mock.get(LATEST_GEOJSON_URL, json=LATEST)
    entry = _entry(hass, sensors=["0001"])
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # 1.42 m is above a 1.0 m watch level
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": "number.graiguenamanagh_watch_level", "value": 1.0},
        blocking=True,
    )
    await hass.async_block_till_done()
    assert hass.states.get("sensor.graiguenamanagh_flood_stage").state == "watch"
    assert hass.states.get("binary_sensor.graiguenamanagh_flood_alert").state == "on"

    # Only alert at "alert" or above
    result = await hass.config_entries.options.async_init(entry.entry_id)
    await hass.config_entries.options.async_configure(
        result["flow_id"], {"scan_interval": 15, "stale_hours": 3, "alert_stage": "alert"}
    )
    await hass.async_block_till_done()
    assert entry.runtime_data.alert_stage == "alert"
    assert hass.states.get("sensor.graiguenamanagh_flood_stage").state == "watch"
    assert hass.states.get("binary_sensor.graiguenamanagh_flood_alert").state == "off"
