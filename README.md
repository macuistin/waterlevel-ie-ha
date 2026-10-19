<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="custom_components/waterlevel_ie/brand/dark_logo.png">
    <img src="custom_components/waterlevel_ie/brand/logo.png" alt="Water Level IE" height="96">
  </picture>
</p>

# Water Level IE for Home Assistant

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-41BDF5.svg)](https://hacs.xyz/docs/faq/custom_repositories/)
[![GitHub release](https://img.shields.io/github/v/release/macuistin/waterlevel-ie-ha)](https://github.com/macuistin/waterlevel-ie-ha/releases)
[![Tests](https://github.com/macuistin/waterlevel-ie-ha/actions/workflows/tests.yml/badge.svg)](https://github.com/macuistin/waterlevel-ie-ha/actions/workflows/tests.yml)
[![License](https://img.shields.io/github/license/macuistin/waterlevel-ie-ha)](LICENSE)

A custom Home Assistant integration that pulls real-time river gauge data from [waterlevel.ie](https://waterlevel.ie), operated by the Irish Office of Public Works (OPW).

> **Unofficial.** This is a community-built integration. It is not made, endorsed or supported by waterlevel.ie or the Office of Public Works; it only reads the open data they publish. Report problems with the integration on this repository's [issue tracker](https://github.com/macuistin/waterlevel-ie-ha/issues), not to the OPW.

Each gauge station becomes a **device** in Home Assistant with all its sensors, flood alerts, and threshold controls grouped underneath.

## Use cases

- Get a phone notification when the river near your home, farm or business passes a level you choose.
- Check whether a river is safe to paddle, fish or walk beside before you set off.
- Chart river levels next to rainfall from a weather integration to see how fast a catchment responds.
- Show gauges on a map card, or watch a remote gauge's battery and signal.

---

## Features

- Browse and add any of the ~500+ Irish river gauge stations
- Search by river (Barrow, Shannon, Boyne, etc.) then pick a station, or enter a station number directly
- Auto-detects which sensors are available for each station
- Sensors poll every 15 minutes (matching the gauge update frequency), reading one shared OPW latest-readings file for all your stations to keep requests to waterlevel.ie low
- Add as many stations as you like — each becomes its own device
- No YAML configuration required

---

## Entities

### Sensors

| Sensor | Unit | Description |
|---|---|---|
| Water Level | m | Live staff gauge level (relative to station datum), updated every 15 min |
| Backup Water Level | m | Reading from the station's second level sensor, where it has one |
| Water Level OD | m | Ordnance datum level — absolute height above Malin Head or Poolbeg sea level |
| Temperature | °C | Sensor housing temperature. May read high in warm weather; not true water temperature |
| Battery Voltage | V | Gauge battery level — useful for alerting on low charge. Diagnostic, disabled by default |
| Signal Strength | dBm | Gauge's mobile signal. Diagnostic, disabled by default |
| Daily Mean Water Level | m | Rolling daily mean from the OPW summary CSV |
| Daily Min Water Level | m | Rolling daily minimum |
| Daily Max Water Level | m | Rolling daily maximum |
| Flood Stage | — | Current flood stage: `normal`, `watch`, `alert`, or `serious` |
| Last Reading | — | Time of the station's newest reading from any sensor |
| Data Age | min | Minutes since that reading. Diagnostic |

Not all sensors are present at every station. The integration probes the API on setup and only creates entities for sensors that actually return data. To turn on a disabled sensor, open it from the device page and enable it in its settings.

Live sensors carry these attributes: `last_reported` (the time of the reading), `week_min` and `week_max` (the range over the last 7 days, on Water Level), and `trend` (`rising`, `steady` or `falling` against yesterday's mean, on Water Level). Water levels can be shown in other length units from each entity's settings.

### Binary Sensor

| Entity | Description |
|---|---|
| Flood Alert | `On` when flood stage is watch, alert, or serious. Use this to trigger automations or notifications |
| Data Stale | `On` when the station has sent nothing for 3 hours. Diagnostic; stays available during an outage so you can alert on it |

### Number Entities (Flood Thresholds)

Each station has three adjustable threshold entities that control when the flood stage changes:

| Entity | Default | Description |
|---|---|---|
| Watch Level | 1.50 m | River rising — worth monitoring |
| Alert Level | 2.50 m | Significant rise — prepare |
| Serious Level | 3.50 m | Major flooding in progress |

These are set per-station and persist across restarts. Adjust them directly from the device page in **Settings → Devices & Services**, or use them in dashboards as interactive controls. Default values are based on OPW flood relief scheme engineering data for the River Barrow — tune them to match your specific gauge and local conditions.

### Map Support

All entities expose `latitude` and `longitude` as state attributes. Stations appear automatically on Home Assistant map cards without any extra configuration.

---

## Supported stations

Any gauge station published on [waterlevel.ie](https://waterlevel.ie/hydro-data/), which covers the OPW's hydrometric network across Ireland (about 500 stations). Stations show the sensors listed above where the gauge has them; most have a water level, and many also report temperature, battery and signal. Stations run by other bodies (for example the EPA or local authorities) are only included where waterlevel.ie publishes them.

---

## Installation

### Via HACS (recommended)

1. In Home Assistant, open **HACS → Integrations**
2. Click the three-dot menu → **Custom repositories**
3. Add `https://github.com/macuistin/waterlevel-ie-ha` as an **Integration**
4. Search for "Water Level IE" and install it
5. Restart Home Assistant

### Manual

1. Download or clone this repository
2. Copy the `custom_components/waterlevel_ie/` folder into your HA `config/custom_components/` directory
3. Restart Home Assistant

---

## Configuration

After installation and restart:

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **Water Level IE**
3. Choose how to find the station:
   - **Browse by river**: pick a river, then a station on it. Stations you've already added are left out of the list.
   - **Enter a station number**: type the number (e.g. `14029`) or paste the station's page address from waterlevel.ie (e.g. `https://waterlevel.ie/0000014029/`)
4. The integration detects available sensors and creates a device

To add more stations, repeat from step 1 — each station is a separate integration entry and device.

| Setup field | What to enter |
|---|---|
| River | The river or catchment the gauge is on, as waterlevel.ie lists it |
| Station | A gauge on that river. Stations you've already added are left out |
| Station number or address | The number from the station's waterlevel.ie page (`14029`, or `0000014029`), or the page's address |

There are no other options; the flood thresholds are set on the device page after setup.

Find station numbers and rivers at [waterlevel.ie/group/list](https://waterlevel.ie/group/list/).

---

## How the data is updated

Every 15 minutes, the integration reads OPW's latest-readings file, which holds the newest reading of every sensor on every station. It is downloaded at most once every 15 minutes and shared by all your stations, so adding more stations doesn't add more of these downloads. For Water Level, each station also reads its last 7 days to work out `week_min` and `week_max`, and uses whichever reading is newer. A sensor missing from the shared file falls back to that station's readings for today, then for the last 7 days. The daily mean, min and max come from the station's summary file, which waterlevel.ie updates once a day, so they describe yesterday; it is re-read at most once an hour. Coordinates come from the waterlevel.ie station map when the station is added.

The flood stage and Flood Alert are worked out in Home Assistant from the latest Water Level and your thresholds; changing a threshold updates them straight away without a download.

If waterlevel.ie can't be reached or a gauge stops reporting, the sensors keep their last reading (its `last_reported` attribute and Data Age show how old it is). Once the station has sent nothing for 3 hours, Data Stale turns on and the other entities become **unavailable** until a new reading arrives; Last Reading, Data Age and Data Stale stay available. The integration logs the outage once and logs again when it recovers. If only the water level is missing, Flood Stage and Flood Alert show unknown rather than "normal".

---

## Automation examples

Notify when the river reaches the alert stage:

```yaml
automation:
  - alias: River at alert level
    triggers:
      - trigger: state
        entity_id: sensor.graiguenamanagh_flood_stage
        to: ["alert", "serious"]
    actions:
      - action: notify.mobile_app_my_phone
        data:
          title: River level
          message: >
            {{ state_attr(trigger.entity_id, 'friendly_name') }} is
            {{ trigger.to_state.state }}
            ({{ states('sensor.graiguenamanagh_water_level') }} m).
```

Notify when the river is rising quickly past the watch level:

```yaml
automation:
  - alias: River rising above watch level
    triggers:
      - trigger: state
        entity_id: binary_sensor.graiguenamanagh_flood_alert
        to: "on"
    conditions:
      - condition: state
        entity_id: sensor.graiguenamanagh_water_level
        attribute: trend
        state: rising
    actions:
      - action: notify.notify
        data:
          message: The river is above its watch level and still rising.
```

Warn when a gauge has stopped reporting:

```yaml
automation:
  - alias: Gauge offline
    triggers:
      - trigger: state
        entity_id: binary_sensor.graiguenamanagh_data_stale
        to: "on"
    actions:
      - action: notify.notify
        data:
          message: The Graiguenamanagh gauge has not reported for 3 hours.
```

---

## Known limitations

- Levels are relative to each station's own datum, so 2 m at one station says nothing about 2 m at another. Use Water Level OD to compare stations.
- The default thresholds (1.5, 2.5, 3.5 m) come from one River Barrow station and will be wrong elsewhere. Set them from the station's own history before relying on Flood Alert.
- Temperature is the gauge housing's temperature, not the water's.
- Gauges report every 15 minutes, and readings can arrive late or be revised by the OPW, so the latest value can be up to about half an hour old.
- The river and station lists during setup are read from waterlevel.ie's web pages. If the site's layout changes, browsing by river can fail; adding by station number still works.
- Each station with a Water Level still downloads its own week of readings every 15 minutes (plus its summary hourly). The latest readings themselves are shared across all stations.

---

## Troubleshooting

**Entities are unavailable.** The station has sent no reading for 3 hours, or waterlevel.ie can't be reached. Data Stale and Data Age show how long it has been. Check [the station's page](https://waterlevel.ie/hydro-data/) in a browser. The entities come back on their own at the next successful update.

**"Could not load the river list".** waterlevel.ie's river pages didn't load. Try again later, or choose **Enter a station number** instead.

**"No sensor data found for that station".** The number doesn't match a station that publishes data. Copy the address from the station's page on waterlevel.ie and paste it in.

**Flood Stage is wrong for my river.** The thresholds are set per station on the device page; the defaults aren't tuned to your gauge.

**Battery Voltage or Signal Strength is missing.** They're disabled by default. Enable them from the device page.

**Something else.** Download diagnostics from **Settings → Devices & Services → Water Level IE → ⋮ → Download diagnostics**, turn on debug logging from the same menu, reproduce the problem, and attach both to an [issue](https://github.com/macuistin/waterlevel-ie-ha/issues).

---

## Removing the integration

1. Go to **Settings → Devices & Services → Water Level IE**.
2. For each station, open the **⋮** menu and choose **Delete**. This removes the station's device and entities.
3. To remove the code as well, uninstall **Water Level IE** in HACS (or delete `config/custom_components/waterlevel_ie/` for a manual install), then restart Home Assistant.

---

## Data & Attribution

Data provided by [waterlevel.ie](https://waterlevel.ie), operated by the [Office of Public Works (OPW)](https://www.opw.ie), Ireland.

Licensed under [Creative Commons Attribution 4.0 (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/).

Suggested attribution: *Contains Irish Public Sector Information licensed under a Creative Commons Attribution 4.0 International (CC BY 4.0) licence (source http://waterlevel.ie — provided by the Office of Public Works.)*

Please do not poll more frequently than every 15 minutes. See [waterlevel.ie/page/api](https://waterlevel.ie/page/api/) for full API terms.

---

## Contributing

Issues and PRs welcome. Open an issue at the [issue tracker](https://github.com/macuistin/waterlevel-ie-ha/issues).

Run the same checks as CI before opening a PR:

```bash
pip install -r requirements_test.txt
ruff check . && ruff format --check .
pytest --cov
```

CI also runs [hassfest](https://developers.home-assistant.io/blog/2020/04/16/hassfest/) and the [HACS action](https://hacs.xyz/docs/publish/action/), and runs the tests against the minimum Home Assistant version in `hacs.json` as well as the latest release.
