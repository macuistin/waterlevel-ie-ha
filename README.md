# Waterlevel.ie for Home Assistant

A custom Home Assistant integration that pulls real-time river gauge data from [waterlevel.ie](https://waterlevel.ie), operated by the Irish Office of Public Works (OPW).

Each gauge station becomes a **device** in Home Assistant with its available sensors grouped underneath — water level, ordnance datum level, temperature, and battery voltage.

---

## Features

- Browse and add any of the ~500+ Irish river gauge stations
- Search by river (Barrow, Shannon, Boyne, etc.) then pick a station, or enter a station number directly
- Auto-detects which sensors are available for each station
- Sensors poll every 15 minutes (matching the gauge update frequency)
- Add as many stations as you like — each becomes its own device
- No YAML configuration required

## Sensors

| Sensor | Unit | Description |
|---|---|---|
| Water Level | m | Staff gauge level (relative to station datum) |
| Water Level OD | m | Ordnance datum level (absolute height, Malin Head or Poolbeg) |
| Temperature | °C | Sensor temperature — note: may read high in warm weather, not true water temperature |
| Battery Voltage | V | Gauge battery — useful for alerting on low charge |

Not all sensors are present at every station. The integration probes the API on setup and only creates entities for sensors that actually return data.

---

## Installation

### Via HACS (recommended)

1. In Home Assistant, open **HACS → Integrations**
2. Click the three-dot menu → **Custom repositories**
3. Add `https://github.com/YOUR_GITHUB_USERNAME/waterlevel-ie-ha` as an **Integration**
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

Issues and PRs welcome. Open an issue at the [issue tracker](https://github.com/YOUR_GITHUB_USERNAME/waterlevel-ie-ha/issues).
