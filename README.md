# Waterlevel.ie for Home Assistant

A custom Home Assistant integration that pulls real-time river gauge data from [waterlevel.ie](https://waterlevel.ie), operated by the Irish Office of Public Works (OPW).

Each gauge station becomes a **device** in Home Assistant with all its sensors, flood alerts, and threshold controls grouped underneath.

---

## Features

- Browse and add any of the ~500+ Irish river gauge stations
- Search by river (Barrow, Shannon, Boyne, etc.) then pick a station, or enter a station number directly
- Auto-detects which sensors are available for each station
- Sensors poll every 15 minutes (matching the gauge update frequency)
- Add as many stations as you like — each becomes its own device
- No YAML configuration required

---

## Entities

### Sensors

| Sensor | Unit | Description |
|---|---|---|
| Water Level | m | Live staff gauge level (relative to station datum), updated every 15 min |
| Water Level OD | m | Ordnance datum level — absolute height above Malin Head or Poolbeg sea level |
| Temperature | °C | Sensor housing temperature. May read high in warm weather; not true water temperature |
| Battery Voltage | V | Gauge battery level — useful for alerting on low charge |
| Daily Mean Water Level | m | Rolling daily mean from the OPW summary CSV |
| Daily Min Water Level | m | Rolling daily minimum |
| Daily Max Water Level | m | Rolling daily maximum |
| Flood Stage | — | Current flood stage: `normal`, `watch`, `alert`, or `serious` |

Not all sensors are present at every station. The integration probes the API on setup and only creates entities for sensors that actually return data.

### Binary Sensor

| Entity | Description |
|---|---|
| Flood Alert | `On` when flood stage is watch, alert, or serious. Use this to trigger automations or notifications |

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

## Installation

### Via HACS (recommended)

1. In Home Assistant, open **HACS → Integrations**
2. Click the three-dot menu → **Custom repositories**
3. Add `https://github.com/macuistin/waterlevel-ie-ha` as an **Integration**
4. Search for "Waterlevel.ie" and install it
5. Restart Home Assistant

### Manual

1. Download or clone this repository
2. Copy the `custom_components/waterlevel_ie/` folder into your HA `config/custom_components/` directory
3. Restart Home Assistant

---

## Configuration

After installation and restart:

1. Go to **Settings → Devices & Services → Add Integration**
2. Search for **Waterlevel.ie**
3. Either:
   - Pick a **river** from the dropdown → select a station on the next screen
   - Or enter a **station number** directly (e.g. `14029`)
4. The integration detects available sensors and creates a device

To add more stations, repeat from step 1 — each station is a separate integration entry and device.

Find station numbers and rivers at [waterlevel.ie/group/list](https://waterlevel.ie/group/list/).

---

## Data & Attribution

Data provided by [waterlevel.ie](https://waterlevel.ie), operated by the [Office of Public Works (OPW)](https://www.opw.ie), Ireland.

Licensed under [Creative Commons Attribution 4.0 (CC BY 4.0)](https://creativecommons.org/licenses/by/4.0/).

Suggested attribution: *Contains Irish Public Sector Information licensed under a Creative Commons Attribution 4.0 International (CC BY 4.0) licence (source http://waterlevel.ie — provided by the Office of Public Works.)*

Please do not poll more frequently than every 15 minutes. See [waterlevel.ie/page/api](https://waterlevel.ie/page/api/) for full API terms.

---

## Contributing

Issues and PRs welcome. Open an issue at the [issue tracker](https://github.com/macuistin/waterlevel-ie-ha/issues).
