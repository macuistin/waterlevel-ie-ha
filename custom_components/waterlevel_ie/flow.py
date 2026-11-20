"""Where the current flow sits in a station's record.

OPW Hydro-Data publishes, for each station with a flow record, the flows exceeded 1, 5,
10, 25, 50, 75, 80, 90, 95 and 99% of the time. A flow's percentile is the share of the
time the flow is at or below it, so 50 is the median, and it is read off those published
flows. Nothing is extrapolated: a flow beyond the highest or lowest published one gives
the nearest end, 99 or 1.
"""

from __future__ import annotations

from itertools import pairwise
from math import log


def flow_percentile(flow: float | None, percentiles: dict[str, float]) -> float | None:
    """The percentile (1 to 99) of a flow in m³/s, or None when it can't be worked out.

    percentiles is {"p05": flow exceeded 5% of the time, ...}. Between two published
    flows the percentile is interpolated on the logarithm of the flow, which suits a
    quantity that spans orders of magnitude. Returns None when the flow is missing or
    the published flows are too few or out of order.
    """
    if flow is None:
        return None
    # Share of the time at or below each flow, lowest flow first
    points = sorted((100 - int(name[1:]), value) for name, value in percentiles.items())
    if len(points) < 3 or any(low[1] >= high[1] for low, high in pairwise(points)):
        return None
    if flow <= points[0][1]:
        return float(points[0][0])
    if flow >= points[-1][1]:
        return float(points[-1][0])
    for (low_pct, low), (high_pct, high) in pairwise(points):
        if flow <= high:
            fraction = (log(flow) - log(low)) / (log(high) - log(low))
            return round(low_pct + fraction * (high_pct - low_pct), 1)
    return None  # pragma: no cover
