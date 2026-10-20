# Changelog

What each release changed, newest first.

## Unreleased

**Added**
- An Irish (Gaeilge) translation. It is a first draft and corrections from fluent speakers are welcome.

## v1.2.1

**Changed**
- Temperature is disabled by default on newly added stations, because it reads the gauge housing rather than the water. Enable it from the device page if you want it. Stations you already have keep it.

**Fixes**
- A station that has stopped reporting, such as one whose last reading is days old, now finishes setting up instead of failing with "no reading newer than 3 hours". Its readings show as unavailable, Data Stale is on, and Last Reading shows when it last reported. It updates on its own once the gauge reports again.
- **Browse by river** could say it couldn't load the river list. It now reads waterlevel.ie's river and station links whether they are relative or absolute.

## v1.2.0

The first tagged release. It reads one shared OPW feed instead of a file per sensor, keeps readings through short outages, suggests flood thresholds from each station's own history, and adds discharge, a level trend, station options, a "near me" search and a flood warning blueprint. Home Assistant 2025.2.0 or newer is required. After upgrading, compare your flood thresholds with their new `suggested_value` attribute, because existing stations keep the thresholds they had.

**Added**
- Find stations near a location when adding a station. It starts at your home location with a 15 km radius and lists stations closest first, with their distance.
- Flood thresholds suggested from each station's OPW Hydro-Data record: the daily mean levels exceeded on 5%, 1% and 0.1% of days. Thresholds you have never set move to them; ones you have set are kept. Each threshold shows a `suggested_value` attribute, and Flood Stage shows `typical_level_m` and `record_daily_mean_m`. See [Number Entities](README.md#number-entities-flood-thresholds).
- **Water Level Trend** (m/h), fitted over the last 3 hours of readings, and **Discharge** (m³/s) from OPW Hydro-Data where the station still reports flow.
- **Last Reading**, **Data Age** and **Data Stale**, which show how fresh a station's data is and stay available during an outage.
- Station options under **Configure**: update interval (15 to 180 minutes), how long without a reading counts as stale (default 3 hours), the lowest stage that turns Flood Alert on (default watch), and re-checking the station for new sensors.
- A **River flood warning** automation blueprint, a dashboard example and a warning that this is not a flood warning service.
- Diagnostics download, translated entity names and states, and icons for every entity.
- A brand icon and logo.

**Changed**
- The integration is called **Water Level IE**, and the README says it is an unofficial community integration that reads waterlevel.ie's open data. The domain, unique ids and entity ids are unchanged.
- Current readings come from OPW's latest-readings file, downloaded at most once every 15 minutes and shared by all your stations. Each station still reads its week of water levels, and its daily summary at most once an hour.
- Through an outage, sensors keep their last reading for up to 3 hours (the stale option), then become unavailable. Flood Stage and Flood Alert are unknown, not "normal", when there is no water level.
- Adding a station starts with a menu: near a location, by river, or by station number. A station number can be typed with or without leading zeros, or pasted as the station's page address. Stations you've already added are left out of the lists.
- The `last_reported` attribute is ISO 8601 with a time zone, for example `2026-10-19T18:15:00+00:00`.
- Water levels use the distance device class, so they can be shown in other length units. Battery Voltage and Signal Strength are diagnostic and disabled by default, and the thresholds are configuration entities.
- Changing a threshold updates Flood Stage and Flood Alert straight away, without downloading anything.

**Fixes**
- Errors in the setup flow stay on the step that caused them, and an unreachable river list no longer leaves an empty, unsubmittable dropdown.
- Coordinates are found whether or not the station number is zero-padded.

**Upgrading**
- Home Assistant 2025.2.0 or newer is required.
- Stations you already have keep their thresholds, including the 1.5, 2.5 and 3.5 m defaults, because Home Assistant restores them. Compare each with its new `suggested_value` attribute on the device page and set the ones you want. Stations you add from now on start at the suggestions.
- To get Discharge or another new sensor on a station you already have, open **Configure** and tick **Check the station for new sensors**.
- An automation that waited for `unavailable` on Water Level now fires after 3 hours without data (the stale option), not on the first failed update. Data Stale is the clearer trigger.

## v1.1.0

**Fixes**
- GPS coordinates are found for every station, and the setup dropdowns work.
- The flood thresholds have defaults based on OPW engineering data for the River Barrow.

## v1.0

The first version: water level, temperature, battery and signal sensors for any waterlevel.ie station, daily mean, min and max, a flood stage with adjustable thresholds, and a flood alert.
