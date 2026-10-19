"""Constants for the Waterlevel.ie integration."""

DOMAIN = "waterlevel_ie"

BASE_URL = "https://waterlevel.ie/data/{period}/{station}_{sensor}.csv"
STATION_PAGE_URL = "https://waterlevel.ie/{station_padded}/"
SUMMARY_URL = "https://waterlevel.ie/{station_padded}/{sensor}/summary/download/data.csv"
GEOJSON_URL = "https://waterlevel.ie/geojson/"
# Latest reading of every sensor on every station, in one file
LATEST_GEOJSON_URL = "https://waterlevel.ie/geojson/latest/"
GROUP_LIST_URL = "https://waterlevel.ie/group/list/"
GROUP_URL = "https://waterlevel.ie/group/{group_id}/"
HYDRO_DATA_URL = "https://waterlevel.ie/hydro-data/"
# OPW Hydro-Data time series for a station. param: S (level) or Q (flow);
# period: week (15-minute values) or year (daily values over the full record)
HYDRO_SERIES_URL = (
    "https://waterlevel.ie/hydro-data/data/internet/stations/0/{station}/{param}/{period}.json"
)

# Live 15-minute sensors (fetched from the day/week CSV), by OPW sensor code.
# Names, units and device classes are in sensor.py, names and icons in strings.json
# and icons.json.
SENSOR_TYPES = ("0001", "0011", "OD", "0002", "0003", "0014")

# The sensor code that carries the main water level and drives the flood stage
WATER_LEVEL = "0001"

FLOOD_STAGES = ["normal", "watch", "alert", "serious"]

# Flood threshold number entities, one per stage above normal.
# Default values are reasonable starting points for Irish river stations based on OPW
# flood relief scheme engineering data for the River Barrow (Graiguenamanagh FRS).
# Each station's datum is different, so tune these to your specific gauge.
THRESHOLD_ENTITIES = {
    "watch": {"min": 0.0, "max": 15.0, "default": 1.50},
    "alert": {"min": 0.0, "max": 15.0, "default": 2.50},
    "serious": {"min": 0.0, "max": 15.0, "default": 3.50},
}

# Daily aggregate sensors derived from the summary CSV (Datetime,Value,Min,Mean,Max)
# Key format: "{source_sensor}_daily_{stat}"
SUMMARY_SENSOR_TYPES = {
    "0001_daily_mean": {"summary_source": "0001", "summary_col": "mean"},
    "0001_daily_min": {"summary_source": "0001", "summary_col": "min"},
    "0001_daily_max": {"summary_source": "0001", "summary_col": "max"},
}

CONF_STATION = "station"
CONF_STATION_NAME = "station_name"
CONF_AVAILABLE_SENSORS = "available_sensors"
CONF_LATITUDE = "latitude"
CONF_LONGITUDE = "longitude"
# Flood thresholds suggested from the station's own history (see thresholds.py)
CONF_SUGGESTED_THRESHOLDS = "suggested_thresholds"

# Options (Settings → Devices & services → Configure)
CONF_SCAN_INTERVAL = "scan_interval"  # minutes
CONF_STALE_HOURS = "stale_hours"
CONF_REDETECT = "redetect_sensors"
DEFAULT_SCAN_INTERVAL = 15
MIN_SCAN_INTERVAL = 15  # OPW asks for no more than one fetch per 15 minutes
DEFAULT_STALE_HOURS = 3
# Lowest flood stage that turns the Flood Alert binary sensor on
CONF_ALERT_STAGE = "alert_stage"
DEFAULT_ALERT_STAGE = "watch"
