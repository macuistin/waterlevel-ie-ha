"""Tests for the water level trend rate and the discharge sensor."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.waterlevel_ie.api import Reading, WaterlevelApi
from custom_components.waterlevel_ie.const import LATEST_GEOJSON_URL
from custom_components.waterlevel_ie.coordinator import trend_rate

from .test_init import HYDRO_URL, _entry, _feature, _mock_station

START = datetime(2026, 10, 19, 15, 0, tzinfo=UTC)
WEEK_URL = "https://waterlevel.ie/data/week/14029_0001.csv"
FLOW_URL = HYDRO_URL.format(station="14029", param="Q", period="week")
FLOW_WEEK = [
    {
        "ts_shortname": "WEB.Cmd.Runoff",
        "data": [
            ["2026-10-19T18:00:00.000Z", 4.574, 254],
            ["2026-10-19T18:15:00.000Z", 4.606, 254],
        ],
    },
    {"ts_shortname": "WEB.YearHyd.Amax(median)", "data": []},
]


def _rising(n: int, step: float = 0.01) -> list[Reading]:
    """n readings 15 minutes apart, rising by step each time."""
    return [Reading(START + timedelta(minutes=15 * i), 1.0 + i * step) for i in range(n)]


def _week_csv(readings: list[Reading]) -> str:
    return "datetime,value\n" + "".join(
        f"{r.time.strftime('%Y-%m-%dT%H:%M:%SZ')},{r.value:.2f}\n" for r in readings
    )


def test_trend_rate() -> None:
    # 1 cm every 15 minutes is 4 cm an hour
    assert trend_rate(_rising(13)) == 0.04
    # Only the last 3 hours count: a flat start doesn't dilute the rate
    flat = [Reading(START - timedelta(hours=10 - i), 1.0) for i in range(10)]
    assert trend_rate(flat + _rising(13)) == 0.04
    assert trend_rate(_rising(13, step=-0.005)) == -0.02
    # Too few readings, or too short a span
    assert trend_rate(_rising(2)) is None
    assert trend_rate([Reading(START + timedelta(minutes=i), 1.0) for i in range(5)]) is None
    assert trend_rate([]) is None


async def test_detect_finds_discharge(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer: FrozenDateTimeFactory
) -> None:
    aioclient_mock.get(HYDRO_URL.format(station="14019", param="Q", period="week"), json=FLOW_WEEK)
    aioclient_mock.get(
        "https://waterlevel.ie/0000014019/",
        text='<h1>Sensors on Station 14019 Levitstown</h1><a href="/0000014019/0001/week/">L</a>',
    )
    aioclient_mock.get(
        "https://waterlevel.ie/0000014019/0001/summary/download/data.csv", status=404
    )
    api = WaterlevelApi(async_get_clientsession(hass))
    assert await api.detect_station("14019") == ("Levitstown", ["0001", "discharge"])

    # A discontinued flow series isn't offered
    freezer.move_to("2026-11-19T19:00:00Z")
    assert await api.detect_station("14019") == ("Levitstown", ["0001"])


async def test_trend_and_discharge_sensors(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(FLOW_URL, json=FLOW_WEEK)
    aioclient_mock.get(WEEK_URL, text=_week_csv(_rising(13)))
    _mock_station(aioclient_mock)
    aioclient_mock.get(LATEST_GEOJSON_URL, json={"features": []})
    entry = _entry(hass, sensors=["0001", "discharge"])
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    trend = hass.states.get("sensor.graiguenamanagh_water_level_trend")
    assert trend.state == "0.04"
    assert trend.attributes["unit_of_measurement"] == "m/h"
    flow = hass.states.get("sensor.graiguenamanagh_discharge")
    assert flow.state == "4.606"
    assert flow.attributes["unit_of_measurement"] == "m³/s"
    assert flow.attributes["last_reported"] == "2026-10-19T18:15:00+00:00"


async def test_trend_includes_newer_feed_reading(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    # The week CSV ends at 17:45; the feed has a jump at 18:15
    aioclient_mock.get(WEEK_URL, text=_week_csv(_rising(12)))
    _mock_station(aioclient_mock)
    feed = {"features": [_feature("14029", "0001", "1.50", "2026-10-19T18:15:00Z")]}
    aioclient_mock.get(LATEST_GEOJSON_URL, json=feed)
    entry = _entry(hass, sensors=["0001"])
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert hass.states.get("sensor.graiguenamanagh_water_level").state == "1.5"
    assert float(hass.states.get("sensor.graiguenamanagh_water_level_trend").state) > 0.04


async def test_discharge_keeps_last_value_when_hydro_data_fails(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.get(FLOW_URL, status=500)
    _mock_station(aioclient_mock)
    aioclient_mock.get(LATEST_GEOJSON_URL, json={"features": []})
    entry = _entry(hass, sensors=["0001", "discharge"])
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # No flow yet, but the water level keeps the station available
    assert hass.states.get("sensor.graiguenamanagh_discharge").state == "unknown"
    assert hass.states.get("sensor.graiguenamanagh_water_level").state == "1.42"
