"""Flood thresholds suggested from a station's own history.

The default thresholds (1.5 / 2.5 / 3.5 m) suit one Barrow gauge, but every gauge
measures from its own datum. OPW Hydro-Data publishes each station's daily mean
level over its full record, on an absolute datum. Lining that up with the live
staff-gauge readings gives the offset between the two, so the history can be
expressed in the same units as the Water Level sensor.
"""

from __future__ import annotations

from statistics import median, quantiles

from .api import Reading

# Daily mean levels this often exceeded set each stage
PERCENTILES = {"watch": 95.0, "alert": 99.0, "serious": 99.9}
# Need at least this much history for the percentiles to mean anything
MIN_DAYS = 365
# ...and this many matching timestamps to trust the datum offset
MIN_MATCHES = 4


def alarm_thresholds(levels: dict[str, float]) -> dict[str, float] | None:
    """Watch / alert / serious levels from a station's OPW alarm levels, in staff units.

    watch is the level exceeded 1% of the time (about bankfull), alert the median of
    the yearly highest levels (a typical big flood) and serious the highest yearly
    maximum on record. Returns None unless the station has all three, so a partial
    file never gives a lopsided set.
    """
    try:
        watch = levels["p01"]
        alert = levels["annual_max_median"]
        serious = levels["annual_max_highest"]
    except KeyError:
        return None
    # Keep the stages in order
    alert = max(alert, watch)
    return {"watch": watch, "alert": alert, "serious": max(serious, alert)}


def datum_offset(staff: list[Reading], absolute: list[Reading]) -> float | None:
    """Median difference between staff-gauge and absolute readings at the same times."""
    by_time = {r.time: r.value for r in absolute}
    diffs = [r.value - by_time[r.time] for r in staff if r.time in by_time]
    return median(diffs) if len(diffs) >= MIN_MATCHES else None


def suggest_thresholds(daily_means: list[Reading], offset: float) -> dict[str, float] | None:
    """Watch / alert / serious levels, plus the median and record daily mean, in staff units.

    Returns None when there isn't enough history.
    """
    if len(daily_means) < MIN_DAYS:
        return None
    levels = [r.value + offset for r in daily_means]
    cuts = quantiles(levels, n=1000, method="inclusive")
    result = {stage: round(cuts[int(pct * 10) - 1], 2) for stage, pct in PERCENTILES.items()}
    # Keep the stages in order even on a very flat record
    result["alert"] = max(result["alert"], result["watch"])
    result["serious"] = max(result["serious"], result["alert"])
    result["typical"] = round(median(levels), 2)
    result["record"] = round(max(levels), 2)
    return result
