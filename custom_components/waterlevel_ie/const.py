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

# A station's metadata, and its alarm levels (percentiles and annual maxima) for a
# parameter, S (level) or Q (flow), in OPW Hydro-Data's absolute datum
HYDRO_STATION_URL = "https://waterlevel.ie/hydro-data/data/internet/stations/0/{station}/index.json"
HYDRO_ALARM_URL = (
    "https://waterlevel.ie/hydro-data/data/internet/stations/0/{station}/{param}/alarmlevel.json"
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

# Sensors read from OPW Hydro-Data rather than the waterlevel.ie files
HYDRO_SENSOR_TYPES = {
    "discharge": {"hydro_param": "Q", "hydro_series": "WEB.Cmd.Runoff"},
}
# Where the current discharge sits in the station's record, as a percentile
FLOW_PERCENTILE = "flow_percentile"
# Water level rate of change, worked out from the week of readings
TREND_RATE = "trend_rate"

CONF_STATION = "station"
CONF_STATION_NAME = "station_name"
CONF_AVAILABLE_SENSORS = "available_sensors"
CONF_LATITUDE = "latitude"
CONF_LONGITUDE = "longitude"
# Flood thresholds suggested from the station's own history (see thresholds.py)
CONF_SUGGESTED_THRESHOLDS = "suggested_thresholds"
# What Hydro-Data publishes about the station: its details and reference levels
CONF_STATION_INFO = "station_info"
# The station's flow percentiles from Hydro-Data ({} when it publishes none)
CONF_FLOW_PERCENTILES = "flow_percentiles"

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

# Where a station's data comes from. Entries made before there were several sources
# have no CONF_SOURCE and are OPW.
CONF_SOURCE = "source"
SOURCE_OPW = "opw"
SOURCE_EA = "ea"
# Environment Agency (England) stations: the measures read, the RLOI id its flood
# warning pages use, and its stage scale
CONF_LEVEL_MEASURE = "level_measure"
CONF_FLOW_MEASURE = "flow_measure"
CONF_RLOI_ID = "rloi_id"

EA_BASE_URL = "https://environment.data.gov.uk/flood-monitoring"
# The latest reading of every measure on every station, in one call. The API asks for
# this rather than one call per station.
EA_LATEST_URL = f"{EA_BASE_URL}/data/readings?latest&_limit=10000"
EA_STATIONS_URL = f"{EA_BASE_URL}/id/stations"
EA_STATION_URL = f"{EA_BASE_URL}/id/stations/{{ref}}"
EA_READINGS_URL = f"{EA_BASE_URL}/id/measures/{{measure}}/readings"
EA_STATION_PAGE_URL = "https://check-for-flooding.service.gov.uk/station/{rloi_id}"

# SEPA (Scotland) stations: the time series read, by SEPA's time series ids
SOURCE_SEPA = "sepa"
# SEPA limits anonymous access by a daily allowance, so its stations update half as often
SEPA_SCAN_INTERVAL = 30
CONF_LEVEL_TS = "level_ts"
CONF_FLOW_TS = "flow_ts"
SEPA_BASE_URL = "https://timeseries.sepa.org.uk/KiWIS/KiWIS"
# Sent with every request: the KiWIS service, as JSON
SEPA_PARAMS = {"service": "kisters", "type": "queryServices", "datasource": "0", "format": "json"}
