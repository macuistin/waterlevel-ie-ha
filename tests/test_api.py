"""Tests for parsing waterlevel.ie data files."""

from __future__ import annotations

from datetime import UTC, datetime

from custom_components.waterlevel_ie.api import (
    Reading,
    find_station_coords,
    parse_group_stations,
    parse_groups,
    parse_latest,
    parse_latest_stations,
    parse_series,
    parse_station_page,
    parse_summary,
    parse_time,
)

UTC = UTC


def test_parse_time() -> None:
    assert parse_time("2026-10-19T18:15:00Z") == datetime(2026, 10, 19, 18, 15, tzinfo=UTC)
    # Summary CSV style, no zone: treated as UTC
    assert parse_time("2026-10-19 18:15") == datetime(2026, 10, 19, 18, 15, tzinfo=UTC)
    assert parse_time("") is None
    assert parse_time("datetime") is None


def test_parse_series_skips_header_and_blanks() -> None:
    text = "datetime,value\n2026-10-19T18:00:00Z,\n2026-10-19T18:15:00Z,1.42\nbad,row\n"
    assert parse_series(text) == [Reading(datetime(2026, 10, 19, 18, 15, tzinfo=UTC), 1.42)]


def test_parse_summary_uses_last_daily_row() -> None:
    text = (
        "Datetime,Value,Min,Mean,Max\n"
        "2026-10-17,,0.9,1.0,1.1\n"
        "2026-10-18,,1.1,1.2,1.3\n"
        "2026-10-19 18:30,0.420,,,\n"
    )
    assert parse_summary(text) == {"min": 1.1, "mean": 1.2, "max": 1.3}
    assert parse_summary("Datetime,Value,Min,Mean,Max\n2026-10-19 18:30,0.420,,,\n") == {}


def test_parse_latest() -> None:
    geo = {
        "features": [
            {
                "properties": {
                    "station_ref": "0000001041",
                    "sensor_ref": "0001",
                    "datetime": "2026-10-19T19:00:00Z",
                    "value": "0.365",
                }
            },
            {
                "properties": {
                    "station_ref": "0000001041",
                    "sensor_ref": "OD",
                    "datetime": "2026-10-19T19:00:00Z",
                    "value": "30.10",
                }
            },
            # Unusable rows are skipped
            {
                "properties": {
                    "station_ref": "0000001042",
                    "sensor_ref": "0001",
                    "datetime": "2026-10-19T19:00:00Z",
                    "value": "",
                }
            },
            {
                "properties": {
                    "station_ref": "0000001042",
                    "sensor_ref": "0001",
                    "datetime": "not a time",
                    "value": "1.0",
                }
            },
            {"properties": {}},
        ]
    }
    latest = parse_latest(geo)
    assert list(latest) == ["01041"]
    assert latest["01041"]["0001"].value == 0.365
    assert latest["01041"]["OD"].value == 30.1


def test_find_station_coords() -> None:
    geo = {
        "features": [
            {"properties": {"ref": "0000014029"}, "geometry": {"coordinates": [-6.95, 52.54]}}
        ]
    }
    assert find_station_coords(geo, "14029") == (52.54, -6.95)
    assert find_station_coords(geo, "14019") is None


def test_parse_groups_and_stations() -> None:
    assert parse_groups(
        '<a href="/group/7/">River Barrow - Water Levels</a><a href="/group/3/">Shannon</a>'
    ) == {"7": "River Barrow", "3": "Shannon"}
    assert parse_group_stations(
        '<a href="/0000014029/">Graiguenamanagh</a><a href="/0000000000/">Bad</a>'
    ) == {"14029": "Graiguenamanagh"}


def test_parse_station_page() -> None:
    name, codes = parse_station_page(
        "<h1>Sensors on Station 14029 Graiguenamanagh Upper</h1>"
        '<a href="/0000014029/0001/week/">x</a><a href="/0000014029/OD/day/">y</a>'
    )
    assert name == "Graiguenamanagh Upper"
    assert codes == {"0001", "OD"}


def test_parse_latest_stations() -> None:
    geo = {
        "features": [
            {
                "properties": {"station_ref": "0000014029", "station_name": "Graiguenamanagh"},
                "geometry": {"coordinates": [-6.95, 52.54]},
            },
            # No name: a placeholder
            {
                "properties": {"station_ref": "0000001041"},
                "geometry": {"coordinates": [-7.5, 54.8]},
            },
            # Skipped: no location, bad ref
            {"properties": {"station_ref": "0000014019"}, "geometry": None},
            {"properties": {"station_ref": "0000000000"}, "geometry": {"coordinates": [0, 0]}},
            {"properties": {"station_ref": "abc"}, "geometry": {"coordinates": [0, 0]}},
        ]
    }
    assert parse_latest_stations(geo) == {
        "14029": ("Graiguenamanagh", 52.54, -6.95),
        "01041": ("Station 1041", 54.8, -7.5),
    }
