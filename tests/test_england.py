"""Tests for England stations, read from the Environment Agency flood-monitoring API."""

from __future__ import annotations

from typing import Any

import aiohttp
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr, entity_registry as er
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.waterlevel_ie.api import StationNotFound, WaterlevelError
from custom_components.waterlevel_ie.const import (
    DOMAIN,
    EA_LATEST_URL,
    EA_STATION_URL,
)
from custom_components.waterlevel_ie.diagnostics import async_get_config_entry_diagnostics
from custom_components.waterlevel_ie.ea_api import (
    EnvironmentAgencyApi,
    parse_latest,
    parse_readings,
    parse_stage_scale,
    parse_station,
    parse_stations,
    pick_measures,
)
from custom_components.waterlevel_ie.thresholds import stage_scale_thresholds

BASE = "http://environment.data.gov.uk/flood-monitoring/id/measures/"
LEVEL = "3400TH-level-stage-i-15_min-mASD"
FLOW = "3400TH-flow--i-15_min-m3_s"
READINGS_URL = f"https://environment.data.gov.uk/flood-monitoring/id/measures/{LEVEL}/readings"
SCALE = {
    "typical_high": 5.0,
    "typical_low": 3.5,
    "highest_recent": 6.0,
    "max_on_record": 6.1,
    "min_on_record": 0.2,
}


def _measure(notation: str, parameter: str, qualifier: str, unit: str, kind: str = "instantaneous"):
    return {
        "@id": BASE + notation,
        "parameter": parameter,
        "qualifier": qualifier,
        "unitName": unit,
        "valueType": kind,
    }


MEASURES = [
    _measure("3400TH-level-stage-i-15_min-mAOD", "level", "Stage", "mAOD"),
    _measure(LEVEL, "level", "Stage", "mASD"),
    _measure("3400TH-level-downstage-i-15_min-mASD", "level", "Downstream Stage", "mASD"),
    _measure("3400TH-flow-water-i-15_min-m3_s", "flow", "Water", "m3/s"),
    _measure(FLOW, "flow", "", "m3/s"),
    _measure("3400TH-flow--Mean-15_min-m3_s", "flow", "", "m3/s", "mean"),
]


def station_json(**overrides: Any) -> dict[str, Any]:
    item: dict[str, Any] = {
        "stationReference": "3400TH",
        "label": "Kingston",
        "lat": 51.415,
        "long": -0.309,
        "RLOIid": "7267",
        "riverName": "River Thames",
        "town": "Kingston",
        "catchmentName": "Thames from Hurley to Teddington",
        "measures": MEASURES,
        "stageScale": {
            "typicalRangeHigh": 5.0,
            "typicalRangeLow": 3.5,
            "highestRecent": {"value": 6.0},
            "maxOnRecord": {"value": 6.1},
            "minOnRecord": {"value": 0.2},
        },
    }
    item.update(overrides)
    return item


def _reading(measure: str, value: float | str, time: str = "2026-10-19T18:15:00Z") -> dict:
    return {"measure": BASE + measure, "dateTime": time, "value": value}


def _mock_api(
    aioclient_mock: AiohttpClientMocker,
    *,
    level: float = 5.5,
    flow: float | None = 12.5,
    fail: bool = False,
) -> None:
    """Mock the feed, and the station's recent readings, as at 18:15 UTC."""
    aioclient_mock.clear_requests()
    if fail:
        aioclient_mock.get(EA_LATEST_URL.split("?")[0], exc=aiohttp.ClientError("down"))
        aioclient_mock.get(READINGS_URL, exc=aiohttp.ClientError("down"))
        return
    items = [_reading(LEVEL, level)]
    if flow is not None:
        items.append(_reading(FLOW, flow))
    aioclient_mock.get(EA_LATEST_URL.split("?")[0], json={"items": items})
    recent = [
        {"measure": BASE + LEVEL, "dateTime": f"2026-10-19T{h}:{m:02d}:00Z", "value": v}
        for h, m, v in ((17, 15, 5.1), (17, 30, 5.2), (17, 45, 5.3), (18, 0, 5.4), (18, 15, 5.5))
    ]
    aioclient_mock.get(READINGS_URL, json={"items": list(reversed(recent))})


def _mock_api_readings_only(aioclient_mock: AiohttpClientMocker) -> None:
    recent = [{"measure": BASE + LEVEL, "dateTime": "2026-10-19T18:15:00Z", "value": 5.5}]
    aioclient_mock.get(READINGS_URL, json={"items": recent})


def _entry(hass: HomeAssistant, **data: Any) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{DOMAIN}_ea_3400TH",
        title="Kingston",
        data={
            "source": "ea",
            "station": "3400TH",
            "station_name": "Kingston",
            "available_sensors": ["0001", "discharge"],
            "latitude": 51.415,
            "longitude": -0.309,
            "level_measure": LEVEL,
            "flow_measure": FLOW,
            "rloi_id": "7267",
            "station_info": {"details": {"status": "active"}, "levels": SCALE},
            **data,
        },
    )
    entry.add_to_hass(hass)
    return entry


async def _setup(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


# --- parsing ---------------------------------------------------------------------------


def test_pick_measures_prefers_the_stage_datum_and_plain_flow() -> None:
    assert pick_measures(MEASURES) == (LEVEL, FLOW)


def test_pick_measures_falls_back_to_other_stage_kinds() -> None:
    downstream = [m for m in MEASURES if "downstage" in m["@id"]]
    assert pick_measures(downstream) == ("3400TH-level-downstage-i-15_min-mASD", None)
    tidal = [_measure("T-level-tidal_level-i-15_min-mAOD", "level", "Tidal Level", "mAOD")]
    assert pick_measures(tidal) == ("T-level-tidal_level-i-15_min-mAOD", None)
    flow = [_measure("F-flow-water-i-15_min-m3_s", "flow", "Water", "m3/s")]
    assert pick_measures(flow) == (None, "F-flow-water-i-15_min-m3_s")


@pytest.mark.parametrize(
    "measures",
    [
        None,
        [],
        ["junk", {"parameter": "level"}],
        [_measure("G-level-groundwater-i-15_min-mAOD", "level", "Groundwater", "mAOD")],
        [_measure("R-rainfall-tipping_bucket_raingauge-t-15_min-mm", "rainfall", "", "mm")],
        [_measure("F-flow--i-15_min-Ml_d", "flow", "", "Ml/d")],
    ],
)
def test_pick_measures_nothing_usable(measures: Any) -> None:
    assert pick_measures(measures) == (None, None)


def test_parse_latest_skips_bad_items() -> None:
    payload = {
        "items": [
            _reading(LEVEL, 5.5),
            _reading(FLOW, "wet"),
            _reading("a", True),
            _reading("b", 1.0, "not a time"),
            {"dateTime": "2026-10-19T18:15:00Z", "value": 1.0},
            "junk",
        ]
    }
    assert set(parse_latest(payload)) == {LEVEL}
    assert parse_latest(payload)[LEVEL].value == 5.5
    assert parse_latest([]) == {}
    assert parse_latest({"items": "no"}) == {}


def test_parse_readings_is_oldest_first() -> None:
    payload = {
        "items": [
            _reading(LEVEL, 2.0, "2026-10-19T18:15:00Z"),
            _reading(LEVEL, 1.0, "2026-10-19T18:00:00Z"),
            _reading(LEVEL, None, "2026-10-19T17:45:00Z"),
            "junk",
        ]
    }
    assert [r.value for r in parse_readings(payload)] == [1.0, 2.0]
    assert parse_readings({"items": 3}) == []
    assert parse_readings(None) == []


def test_parse_stage_scale() -> None:
    assert parse_stage_scale(station_json()["stageScale"]) == SCALE
    assert parse_stage_scale({"typicalRangeHigh": "high", "highestRecent": {"value": None}}) == {}
    assert parse_stage_scale(None) == {}


def test_parse_station() -> None:
    station = parse_station(station_json())
    assert station is not None
    assert station["station"] == "3400TH"
    assert station["level_measure"] == LEVEL
    assert station["flow_measure"] == FLOW
    assert station["stage_scale"] == SCALE
    assert station["rloi_id"] == "7267"


@pytest.mark.parametrize(
    "item",
    [
        "junk",
        station_json(stationReference=""),
        station_json(lat=None),
        station_json(measures=[]),
    ],
)
def test_parse_station_unusable(item: Any) -> None:
    assert parse_station(item) is None


def test_parse_station_defaults_the_name() -> None:
    station = parse_station(station_json(label=None, RLOIid=None))
    assert station is not None
    assert station["name"] == "Station 3400TH"
    assert station["rloi_id"] == ""


def test_parse_stations_drops_unusable_ones() -> None:
    payload = {"items": [station_json(), station_json(measures=[]), "junk"]}
    assert [s["station"] for s in parse_stations(payload)] == ["3400TH"]
    assert parse_stations({"items": "no"}) == []
    assert parse_stations(None) == []


def test_stage_scale_thresholds() -> None:
    assert stage_scale_thresholds(SCALE) == {"watch": 5.5, "alert": 5.8, "serious": 6.0}
    assert stage_scale_thresholds({"typical_high": 5.0}) is None
    assert stage_scale_thresholds({"typical_high": 5.0, "highest_recent": 5.0}) is None
    assert stage_scale_thresholds({}) is None


# --- the API ---------------------------------------------------------------------------


async def test_api_station_not_found(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    from homeassistant.helpers.aiohttp_client import async_get_clientsession

    api = EnvironmentAgencyApi(async_get_clientsession(hass))
    aioclient_mock.get(EA_STATION_URL.format(ref="NOPE"), status=404)
    with pytest.raises(StationNotFound):
        await api.station("NOPE")
    # A station with no level measure is as good as not found
    aioclient_mock.get(EA_STATION_URL.format(ref="DRY"), json={"items": station_json(measures=[])})
    with pytest.raises(StationNotFound):
        await api.station("DRY")
    aioclient_mock.get(EA_STATION_URL.format(ref="ODD"), json=["not", "a", "dict"])
    with pytest.raises(StationNotFound):
        await api.station("ODD")
    aioclient_mock.get(EA_STATION_URL.format(ref="BAD"), status=500)
    with pytest.raises(WaterlevelError):
        await api.station("BAD")


# --- setup and entities ----------------------------------------------------------------


async def test_setup_creates_a_device_with_the_same_entity_names(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    entity_registry: er.EntityRegistry,
    device_registry: dr.DeviceRegistry,
) -> None:
    _mock_api(aioclient_mock)
    entry = _entry(hass)
    await _setup(hass, entry)
    assert entry.state is ConfigEntryState.LOADED

    # The device registry's lookups are deprecated in newer Home Assistant releases
    (device,) = dr.async_entries_for_config_entry(device_registry, entry.entry_id)
    assert device.identifiers == {(DOMAIN, "ea_3400TH")}
    assert device.manufacturer == "Environment Agency"
    assert device.model == "River gauge station"
    assert device.configuration_url == "https://check-for-flooding.service.gov.uk/station/7267"

    assert hass.states.get("sensor.kingston_water_level").state == "5.5"
    assert hass.states.get("sensor.kingston_discharge").state == "12.5"
    # 5.5 is the watch level, so this is "watch"; thresholds come from the stage scale
    assert hass.states.get("sensor.kingston_flood_stage").state == "watch"
    assert hass.states.get("binary_sensor.kingston_flood_alert").state == "on"
    assert hass.states.get("number.kingston_watch_level").state == "5.5"
    assert hass.states.get("number.kingston_serious_level").state == "6.0"
    assert hass.states.get("binary_sensor.kingston_data_stale").state == "off"
    # 0.1 m every 15 minutes
    assert float(hass.states.get("sensor.kingston_water_level_trend").state) == pytest.approx(
        0.4, abs=0.01
    )
    assert entity_registry.async_get_entity_id("sensor", DOMAIN, f"{DOMAIN}_ea_3400TH_0001"), (
        "unique ids are prefixed with the source"
    )
    assert hass.states.get("sensor.kingston_station_information").state == "stage_scale"


async def test_station_without_flow_has_no_discharge(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    _mock_api(aioclient_mock, flow=None)
    entry = _entry(hass, available_sensors=["0001"], flow_measure=None, rloi_id="")
    await _setup(hass, entry)
    assert hass.states.get("sensor.kingston_discharge") is None
    assert entry.runtime_data.configuration_url is None


async def test_flow_missing_from_the_feed_is_unknown(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    _mock_api(aioclient_mock, flow=None)
    await _setup(hass, _entry(hass))
    assert hass.states.get("sensor.kingston_discharge").state == STATE_UNKNOWN


async def test_without_a_stage_scale_the_flood_entities_are_unknown(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    _mock_api(aioclient_mock)
    entry = _entry(hass, station_info={"details": {}, "levels": {}})
    await _setup(hass, entry)
    assert hass.states.get("sensor.kingston_flood_stage").state == STATE_UNKNOWN
    assert hass.states.get("binary_sensor.kingston_flood_alert").state == STATE_UNKNOWN
    assert hass.states.get("number.kingston_watch_level").state == STATE_UNKNOWN
    assert hass.states.get("sensor.kingston_station_information").state == "fallback"
    # Setting a threshold brings the stage back
    await hass.services.async_call(
        "number",
        "set_value",
        {"entity_id": "number.kingston_watch_level", "value": 5.0},
        blocking=True,
    )
    assert hass.states.get("sensor.kingston_flood_stage").state == "watch"


async def test_trend_fetch_failure_uses_the_feed(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(EA_LATEST_URL.split("?")[0], json={"items": [_reading(LEVEL, 5.5)]})
    aioclient_mock.get(READINGS_URL, exc=aiohttp.ClientError("down"))
    await _setup(hass, _entry(hass))
    assert hass.states.get("sensor.kingston_water_level").state == "5.5"
    assert hass.states.get("sensor.kingston_water_level_trend").state == STATE_UNKNOWN


async def test_newer_recent_reading_wins_over_the_feed(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(
        EA_LATEST_URL.split("?")[0],
        json={"items": [_reading(LEVEL, 5.0, "2026-10-19T17:00:00Z")]},
    )
    _mock_api_readings_only(aioclient_mock)
    await _setup(hass, _entry(hass))
    assert hass.states.get("sensor.kingston_water_level").state == "5.5"


async def test_nothing_ever_read_retries_setup(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    _mock_api(aioclient_mock, fail=True)
    entry = _entry(hass)
    assert not await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_diagnostics(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    _mock_api(aioclient_mock)
    entry = _entry(hass)
    await _setup(hass, entry)
    diagnostics = await async_get_config_entry_diagnostics(hass, entry)
    assert diagnostics["default_thresholds_source"] == "stage_scale"
    assert diagnostics["entry"]["data"]["station"] == "3400TH"
    assert diagnostics["flood_stage"] == "watch"


async def test_options_do_not_offer_redetect(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    _mock_api(aioclient_mock)
    entry = _entry(hass)
    await _setup(hass, entry)
    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert {str(key) for key in result["data_schema"].schema} == {
        "scan_interval",
        "stale_hours",
        "alert_stage",
    }
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"scan_interval": 30, "stale_hours": 3, "alert_stage": "alert"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.options["scan_interval"] == 30
