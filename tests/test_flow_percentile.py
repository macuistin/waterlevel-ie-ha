"""Tests for the Flow Percentile sensor, read from OPW Hydro-Data's flow percentiles."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.waterlevel_ie.api import parse_flow_percentiles
from custom_components.waterlevel_ie.const import LATEST_GEOJSON_URL
from custom_components.waterlevel_ie.flow import flow_percentile

from .test_init import HYDRO_URL, _calls, _entry, _mock_station
from .test_trend_flow import FLOW_URL, FLOW_WEEK

ALARM_URL = HYDRO_URL.format(station="14029", param="Q", period="alarmlevel")

# Flows (m³/s) exceeded this share of the time, as published for one station
FLOWS = {
    1: 16.154,
    5: 10.745,
    10: 8.135,
    25: 4.961,
    50: 3.084,
    75: 2.045,
    80: 1.856,
    90: 1.447,
    95: 1.158,
    99: 0.712,
}
LEVELS = {f"p{pct:02d}": flow for pct, flow in FLOWS.items()}


def _alarm(flows: dict[int, float | None]) -> list[dict]:
    series = [
        {
            "ts_shortname": f"WEB.Percentile-{pct:02d}",
            "data": [["2025-01-01T00:00:00.000Z", 9.9], ["2026-01-01T00:00:00.000Z", flow]]
            if flow
            else [["2026-01-01T00:00:00.000Z", flow]],
        }
        for pct, flow in flows.items()
    ]
    return [
        {"ts_shortname": "WEB.LongTermAverage", "data": [["2026-01-01T00:00:00.000Z", 4.1]]},
        *series,
    ]


def test_parse_flow_percentiles() -> None:
    assert parse_flow_percentiles(_alarm(FLOWS)) == LEVELS
    # Series with no value or no flow are skipped, as is anything that isn't a series
    payload = [*_alarm({5: None, 50: 0.0, 95: 1.2}), "not a series", {"ts_shortname": "x"}]
    assert parse_flow_percentiles(payload) == {"p95": 1.2}
    assert parse_flow_percentiles({"not": "a list"}) == {}


def test_flow_percentile_is_read_off_the_published_flows() -> None:
    # A published flow gives its own percentile: exceeded 50% of the time is the median
    assert flow_percentile(3.084, LEVELS) == 50.0
    assert flow_percentile(10.745, LEVELS) == 95.0
    assert flow_percentile(1.158, LEVELS) == 5.0
    # Between two published flows it is interpolated, and rises with the flow
    assert flow_percentile(4.606, LEVELS) == pytest.approx(71.1, abs=0.05)
    values = [flow_percentile(flow, LEVELS) for flow in (0.9, 1.5, 2.5, 4, 7, 12)]
    assert values == sorted(values)
    assert all(value is not None and 1 < value < 99 for value in values)


def test_flow_percentile_stops_at_the_ends_of_the_record() -> None:
    assert flow_percentile(40.0, LEVELS) == 99.0
    assert flow_percentile(0.3, LEVELS) == 1.0
    assert flow_percentile(0.0, LEVELS) == 1.0


def test_flow_percentile_needs_a_usable_record() -> None:
    assert flow_percentile(None, LEVELS) is None
    assert flow_percentile(3.0, {}) is None
    assert flow_percentile(3.0, {"p50": 3.0, "p05": 10.0}) is None
    # Flows that fall as the exceedance rises are out of order
    assert flow_percentile(3.0, {**LEVELS, "p10": 20.0}) is None


async def test_flow_percentile_sensor(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(FLOW_URL, json=FLOW_WEEK)
    aioclient_mock.get(ALARM_URL, json=_alarm(FLOWS))
    _mock_station(aioclient_mock)
    aioclient_mock.get(LATEST_GEOJSON_URL, json={"features": []})
    entry = _entry(hass, sensors=["0001", "discharge"])
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    percentile = hass.states.get("sensor.graiguenamanagh_flow_percentile")
    assert float(percentile.state) == pytest.approx(71.1, abs=0.05)
    assert percentile.attributes["unit_of_measurement"] == "%"
    assert percentile.attributes["median_flow_m3s"] == 3.084
    assert percentile.attributes["exceeded_5_percent_flow_m3s"] == 10.745
    assert percentile.attributes["exceeded_95_percent_flow_m3s"] == 1.158
    # It is as old as the discharge it comes from
    assert percentile.attributes["last_reported"] == "2026-10-19T18:15:00+00:00"
    assert (
        percentile.attributes["stale"]
        == hass.states.get("sensor.graiguenamanagh_discharge").attributes["stale"]
    )
    assert entry.data["flow_percentiles"] == LEVELS

    # A restart reads them from the entry instead of fetching them again
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert _calls(aioclient_mock, ALARM_URL) == 1
    assert hass.states.get("sensor.graiguenamanagh_flow_percentile") is not None


async def test_no_flow_percentile_without_published_percentiles(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(FLOW_URL, json=FLOW_WEEK)
    aioclient_mock.get(ALARM_URL, json=_alarm({}))
    _mock_station(aioclient_mock)
    aioclient_mock.get(LATEST_GEOJSON_URL, json={"features": []})
    entry = _entry(hass, sensors=["0001", "discharge"])
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.graiguenamanagh_discharge").state == "4.606"
    assert hass.states.get("sensor.graiguenamanagh_flow_percentile") is None
    # "None published" is remembered, so it isn't asked again
    assert entry.data["flow_percentiles"] == {}


async def test_flow_percentiles_are_retried_after_a_failed_fetch(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(FLOW_URL, json=FLOW_WEEK)
    aioclient_mock.get(ALARM_URL, status=500)
    _mock_station(aioclient_mock)
    aioclient_mock.get(LATEST_GEOJSON_URL, json={"features": []})
    entry = _entry(hass, sensors=["0001", "discharge"])
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.graiguenamanagh_flow_percentile") is None
    assert "flow_percentiles" not in entry.data


async def test_stations_without_flow_do_not_fetch_percentiles(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    _mock_station(aioclient_mock)
    aioclient_mock.get(LATEST_GEOJSON_URL, json={"features": []})
    entry = _entry(hass, sensors=["0001"])
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert _calls(aioclient_mock, ALARM_URL) == 0
    assert hass.states.get("sensor.graiguenamanagh_flow_percentile") is None
