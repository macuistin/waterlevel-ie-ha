# Water Level IE

Rules for AI assistants and contributors working in this repo.

## What this is

A Home Assistant custom integration, distributed through HACS, that reads OPW river gauge
data published on waterlevel.ie. It is unofficial: never present it as made or endorsed by
waterlevel.ie or the OPW, and never copy their logos. The display name is "Water Level IE";
the domain is `waterlevel_ie`.

It targets the Gold tier of the Home Assistant quality scale.
`custom_components/waterlevel_ie/quality_scale.yaml` lists each rule as done, exempt or
todo. Keep it true: update it in the same PR as the change, and don't claim a rule the code
doesn't meet.

## Layout

| Path | Role |
| --- | --- |
| `coordinator.py` | Downloads and parses the CSVs every 15 minutes; `flood_stage()` works out the stage from the held reading |
| `config_flow.py` | Menu, browse by river, or enter a station number |
| `entity.py` | `WaterlevelEntity`: unique id, device info, map coordinates |
| `sensor.py` | `SENSOR_DESCRIPTIONS`, keyed by OPW sensor code or summary key |
| `binary_sensor.py`, `number.py` | Flood Alert and the three threshold numbers |
| `diagnostics.py` | Download diagnostics |
| `strings.json`, `icons.json` | Every entity's name and icon, by translation key |
| `brand/` | Icon and logos; sources in `docs/brand/` |

## Rules

- Unique ids (`f"{DOMAIN}_{station}_{key}"`) never change once released. English entity
  names don't change either, because new installs derive entity ids from them.
- A new entity needs a translation key, a name in `strings.json` and an icon in
  `icons.json`. `translations/en.json` is a byte copy of `strings.json`. `tests/test_metadata.py`
  checks both.
- Every platform sets `PARALLEL_UPDATES`.
- Use `entry.runtime_data`, never `hass.data[DOMAIN]`.
- Use `async_get_clientsession(hass)`; never open an `aiohttp.ClientSession`.
- Don't poll waterlevel.ie more often than every 15 minutes (their API terms).
- When every live fetch fails, the coordinator raises `UpdateFailed` so entities go
  unavailable. Per-sensor failures log at debug, so an outage logs once.
- The minimum Home Assistant version is in `hacs.json`, and `.github/workflows/tests.yml`
  tests it. Change both together (a test checks they match).
- Keep the version in `manifest.json` and `pyproject.toml` the same (a test checks).

## Commands

```bash
pip install -r requirements_test.txt
ruff check . && ruff format --check .
pytest --cov
```

The latest Home Assistant needs Python 3.14. CI also runs the floor in `hacs.json` on
Python 3.13, plus hassfest and the HACS action.
