"""Tests for Scottish stations, read from SEPA's KiWIS time series API."""

from __future__ import annotations

from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import CONF_LOCATION, CONF_RADIUS, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.waterlevel_ie.api import StationNotFound, WaterlevelError
from custom_components.waterlevel_ie.const import DOMAIN, SEPA_BASE_URL
from custom_components.waterlevel_ie.sepa_api import (
    SepaApi,
    parse_series_ids,
    parse_stations,
    parse_values,
)

LEVEL_TS = "57759010"
FLOW_TS = "67777010"
STATIONS = [
    ["station_no", "station_name", "station_latitude", "station_longitude", "river_name"],
    ["234253", "Kilphedir", "58.14", "-3.70", "Helmsdale"],
    ["999", "Far away", "55.0", "-3.0", "Annan"],
    ["", "No number", "58.1", "-3.7", "X"],
    ["123", "No place", None, None, "Y"],
]
LOCATION = {CONF_LOCATION: {"latitude": 58.14, "longitude": -3.70, CONF_RADIUS: 5000}}


def _url(request: str) -> str:
    return f"{SEPA_BASE_URL}?request={request}"


def _values(level: list[tuple[str, float]], flow: list[tuple[str, float]] | None = None) -> list:
    def series(ts_id: str, rows: list[tuple[str, float]]) -> dict[str, Any]:
        return {"ts_id": ts_id, "columns": "Timestamp,Value", "data": [list(r) for r in rows]}

    return [series(LEVEL_TS, level)] + ([series(FLOW_TS, flow)] if flow is not None else [])


RECENT = [
    ("2026-10-19T17:15:00.000Z", 0.90),
    ("2026-10-19T17:30:00.000Z", 1.00),
    ("2026-10-19T17:45:00.000Z", 1.10),
    ("2026-10-19T18:00:00.000Z", 1.20),
    ("2026-10-19T18:15:00.000Z", 1.30),
]


def _mock_api(
    aioclient_mock: AiohttpClientMocker,
    *,
    level: list[tuple[str, float]] = RECENT,
    flow: list[tuple[str, float]] | None = (("2026-10-19T18:15:00.000Z", 7.5),),  # type: ignore[assignment]
    clear: bool = True,
) -> None:
    if clear:
        aioclient_mock.clear_requests()
    aioclient_mock.get(_url("getTimeseriesValues"), json=_values(level, flow))
    # The station's daily record, for the suggested thresholds
    aioclient_mock.get(_url("getTimeseriesList"), json=[["ts_id", "station_no"]])


def _entry(hass: HomeAssistant, **data: Any) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{DOMAIN}_sepa_234253",
        title="Kilphedir",
        data={
            "source": "sepa",
            "station": "234253",
            "station_name": "Kilphedir",
            "available_sensors": ["0001", "discharge"],
            "latitude": 58.14,
            "longitude": -3.70,
            "level_ts": LEVEL_TS,
            "flow_ts": FLOW_TS,
            "station_info": {"details": {"river": "Helmsdale"}},
            "suggested_thresholds": {"watch": 1.5, "alert": 2.5, "serious": 3.5},
            **data,
        },
    )
    entry.add_to_hass(hass)
    return entry


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


# --- parsing ---------------------------------------------------------------------------


def test_parse_stations_skips_unusable_rows() -> None:
    stations = parse_stations(STATIONS)
    assert [s["station"] for s in stations] == ["234253", "999"]
    assert stations[0] == {
        "station": "234253",
        "name": "Kilphedir",
        "latitude": 58.14,
        "longitude": -3.7,
        "river": "Helmsdale",
    }
    assert parse_stations({"message": "bad"}) == []
    assert parse_stations([["station_no"]]) == []
    assert parse_stations([["station_no"], "junk"]) == []


def test_parse_stations_defaults_the_name() -> None:
    rows = [["station_no", "station_latitude", "station_longitude"], ["5", True, "1.5"]]
    assert parse_stations(rows) == []
    rows = [["station_no", "station_latitude", "station_longitude"], ["5", 1, "x"]]
    assert parse_stations(rows) == []
    rows = [["station_no", "station_latitude", "station_longitude"], ["5", 1, "2.5"]]
    assert parse_stations(rows)[0]["name"] == "Station 5"


def test_parse_series_ids() -> None:
    assert parse_series_ids([["ts_id", "station_no"], ["1", "10"], ["2", ""], ["", "3"]]) == {
        "10": "1"
    }


def test_parse_values() -> None:
    payload = [
        {
            "ts_id": "1",
            "data": [["2026-10-19T18:15:00.000Z", 2.0], ["2026-10-19T18:00:00.000Z", 1]],
        },
        {
            "ts_id": "2",
            "data": [["bad time", 1.0], ["2026-10-19T18:00:00.000Z", None], "junk", [1]],
        },
        {"ts_id": "3", "data": None},
        {"data": []},
        "junk",
    ]
    values = parse_values(payload)
    assert [r.value for r in values["1"]] == [1.0, 2.0]
    assert values["2"] == []
    assert values["3"] == []
    assert set(values) == {"1", "2", "3"}
    assert parse_values({"message": "no"}) == {}


# --- the API ---------------------------------------------------------------------------


async def test_api_errors(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    api = SepaApi(async_get_clientsession(hass))
    aioclient_mock.get(_url("getStationList"), json={"message": "Quota exceeded"})
    with pytest.raises(WaterlevelError, match="Quota exceeded"):
        await api.stations()
    aioclient_mock.get(_url("getTimeseriesValues"), status=500)
    with pytest.raises(WaterlevelError):
        await api.values(["1"], "PT3H")
    aioclient_mock.get(_url("getTimeseriesList"), json=[["ts_id", "station_no"]])
    with pytest.raises(StationNotFound):
        await api.station_series("234253")


async def test_api_station_series(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    api = SepaApi(async_get_clientsession(hass))
    aioclient_mock.get(
        _url("getTimeseriesList") + "&parametertype_name=S",
        json=[["ts_id", "station_no"], [LEVEL_TS, "234253"]],
    )
    aioclient_mock.get(_url("getTimeseriesList") + "&parametertype_name=Q", json=[["ts_id"]])
    assert await api.station_series("234253") == (LEVEL_TS, None)


async def test_api_daily_means(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    api = SepaApi(async_get_clientsession(hass))
    aioclient_mock.get(_url("getTimeseriesList"), json=[["ts_id", "station_no"]])
    assert await api.daily_means("234253") == []
    aioclient_mock.clear_requests()
    aioclient_mock.get(_url("getTimeseriesList"), json=[["ts_id", "station_no"], ["5", "234253"]])
    aioclient_mock.get(
        _url("getTimeseriesValues"),
        json=[{"ts_id": "5", "data": [["2026-10-18T09:00:00.000Z", 0.7]]}],
    )
    assert [r.value for r in await api.daily_means("234253")] == [0.7]


# --- setup and entities ----------------------------------------------------------------


async def test_setup_creates_a_device_with_the_same_entity_names(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    device_registry: dr.DeviceRegistry,
) -> None:
    _mock_api(aioclient_mock)
    entry = _entry(hass)
    await _setup(hass, entry)
    assert entry.state is ConfigEntryState.LOADED

    device = device_registry.async_get_device(identifiers={(DOMAIN, "sepa_234253")})
    assert device is not None
    assert device.manufacturer == "SEPA"
    assert device.model == "River gauge station"
    assert device.configuration_url is None

    assert hass.states.get("sensor.kilphedir_water_level").state == "1.3"
    assert hass.states.get("sensor.kilphedir_discharge").state == "7.5"
    assert hass.states.get("sensor.kilphedir_flood_stage").state == "normal"
    assert hass.states.get("number.kilphedir_watch_level").state == "1.5"
    assert hass.states.get("sensor.kilphedir_station_information").state == "history"
    # 0.1 m every 15 minutes
    assert float(hass.states.get("sensor.kilphedir_water_level_trend").state) == pytest.approx(
        0.4, abs=0.01
    )


async def test_station_without_flow(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    _mock_api(aioclient_mock, flow=None)
    await _setup(hass, _entry(hass, available_sensors=["0001"], flow_ts=None))
    assert hass.states.get("sensor.kilphedir_discharge") is None
    assert hass.states.get("sensor.kilphedir_water_level").state == "1.3"


async def test_flow_missing_from_the_reply_is_unknown(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    _mock_api(aioclient_mock, flow=[])
    await _setup(hass, _entry(hass))
    assert hass.states.get("sensor.kilphedir_discharge").state == STATE_UNKNOWN


async def test_history_gives_the_thresholds_once(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """With no stored suggestion, the daily record is read and the percentiles used."""
    aioclient_mock.get(
        _url("getTimeseriesList") + "&station_no=234253&ts_shortname=HDay.Mean",
        json=[["ts_id", "station_no"], ["5", "234253"]],
    )
    daily = [
        [
            f"{2000 + i // 365}-{(i % 365) // 31 + 1:02d}-{(i % 31) + 1:02d}T09:00:00.000Z",
            1 + i * 0.001,
        ]
        for i in range(400)
    ]
    aioclient_mock.get(
        _url("getTimeseriesValues") + "&period=complete",
        json=[{"ts_id": "5", "data": daily}],
    )
    _mock_api(aioclient_mock, clear=False)
    entry = _entry(hass)
    hass.config_entries.async_update_entry(
        entry, data={k: v for k, v in entry.data.items() if k != "suggested_thresholds"}
    )
    await _setup(hass, entry)
    assert hass.states.get("sensor.kilphedir_station_information").state == "history"
    assert set(entry.data["suggested_thresholds"]) >= {"watch", "alert", "serious"}


async def test_history_failure_is_retried_next_time(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(
        _url("getTimeseriesList") + "&station_no=234253", exc=aiohttp.ClientError("down")
    )
    _mock_api(aioclient_mock, clear=False)
    entry = _entry(hass)
    hass.config_entries.async_update_entry(
        entry, data={k: v for k, v in entry.data.items() if k != "suggested_thresholds"}
    )
    await _setup(hass, entry)
    assert "suggested_thresholds" not in entry.data
    assert hass.states.get("sensor.kilphedir_station_information").state == "fallback"


async def test_no_history_leaves_the_flood_entities_unknown(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    _mock_api(aioclient_mock)
    await _setup(hass, _entry(hass, suggested_thresholds={}))
    assert hass.states.get("sensor.kilphedir_flood_stage").state == STATE_UNKNOWN
    assert hass.states.get("binary_sensor.kilphedir_flood_alert").state == STATE_UNKNOWN


async def test_nothing_ever_read_retries_setup(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(_url("getTimeseriesValues"), exc=aiohttp.ClientError("down"))
    entry = _entry(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_quota_error_keeps_the_last_reading(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    _mock_api(aioclient_mock)
    entry = _entry(hass)
    await _setup(hass, entry)
    aioclient_mock.clear_requests()
    aioclient_mock.get(_url("getTimeseriesValues"), json={"message": "Quota exceeded"})
    await entry.runtime_data.async_refresh()
    assert hass.states.get("sensor.kilphedir_water_level").state == "1.3"
