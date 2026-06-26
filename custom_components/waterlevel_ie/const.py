"""Constants for the Waterlevel.ie integration."""

DOMAIN = "waterlevel_ie"

BASE_URL = "https://waterlevel.ie/data/{period}/{station}_{sensor}.csv"
STATION_PAGE_URL = "https://waterlevel.ie/{station_padded}/"
SUMMARY_URL = "https://waterlevel.ie/{station_padded}/{sensor}/summary/download/data.csv"
GEOJSON_URL = "https://waterlevel.ie/geojson/"
HYDRO_DATA_URL = "https://waterlevel.ie/hydro-data/"

# Live 15-minute sensors (fetched from day/week CSV)
SENSOR_TYPES = {
    "0001": {
        "name": "Water Level",
        "unit": "m",
        "device_class": None,
        "state_class": "measurement",
        "icon": "mdi:waves-arrow-up",
    },
    "0011": {
        "name": "Backup Water Level",
        "unit": "m",
        "device_class": None,
        "state_class": "measurement",
        "icon": "mdi:waves-arrow-up",
    },
    "OD": {
        "name": "Water Level OD",
        "unit": "m",
        "device_class": None,
        "state_class": "measurement",
        "icon": "mdi:elevation-rise",
    },
    "0002": {
        "name": "Temperature",
        "unit": "\u00b0C",
        "device_class": "temperature",
        "state_class": "measurement",
        "icon": "mdi:thermometer-water",
    },
    "0003": {
        "name": "Battery Voltage",
        "unit": "V",
        "device_class": "voltage",
        "state_class": "measurement",
        "icon": "mdi:battery",
    },
    "0014": {
        "name": "Signal Strength",
        "unit": "dBm",
        "device_class": None,
        "state_class": "measurement",
        "icon": "mdi:signal",
    },
}

FLOOD_STAGES = ["normal", "watch", "alert", "serious"]
FLOOD_STAGE_ICONS = {
    "normal": "mdi:waves",
    "watch": "mdi:alert-outline",
    "alert": "mdi:alert",
    "serious": "mdi:alert-octagon",
}

# Flood threshold number entities — one per stage
# Default values are reasonable starting points for Irish river stations based on OPW
# flood relief scheme engineering data for the River Barrow (Graiguenamanagh FRS).
# Each station's datum is different — tune these to your specific gauge.
THRESHOLD_ENTITIES = {
    "watch":   {"name": "Watch Level",   "icon": "mdi:alert-outline",  "min": 0.0, "max": 15.0, "default": 1.50},
    "alert":   {"name": "Alert Level",   "icon": "mdi:alert",          "min": 0.0, "max": 15.0, "default": 2.50},
    "serious": {"name": "Serious Level", "icon": "mdi:alert-octagon",  "min": 0.0, "max": 15.0, "default": 3.50},
}

# Daily aggregate sensors derived from the summary CSV (Datetime,Value,Min,Mean,Max)
# Key format: "{source_sensor}_daily_{stat}"
SUMMARY_SENSOR_TYPES = {
    "0001_daily_mean": {
        "name": "Daily Mean Water Level",
        "unit": "m",
        "device_class": None,
        "state_class": "measurement",
        "icon": "mdi:waves-arrow-up",
        "summary_source": "0001",
        "summary_col": "mean",
    },
    "0001_daily_min": {
        "name": "Daily Min Water Level",
        "unit": "m",
        "device_class": None,
        "state_class": "measurement",
        "icon": "mdi:arrow-collapse-down",
        "summary_source": "0001",
        "summary_col": "min",
    },
    "0001_daily_max": {
        "name": "Daily Max Water Level",
        "unit": "m",
        "device_class": None,
        "state_class": "measurement",
        "icon": "mdi:arrow-collapse-up",
        "summary_source": "0001",
        "summary_col": "max",
    },
}

ALL_SENSOR_TYPES = {**SENSOR_TYPES, **SUMMARY_SENSOR_TYPES}

CONF_STATION = "station"
CONF_STATION_NAME = "station_name"
CONF_AVAILABLE_SENSORS = "available_sensors"
CONF_LATITUDE = "latitude"
CONF_LONGITUDE = "longitude"
