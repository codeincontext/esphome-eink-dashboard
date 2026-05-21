"""Provider-agnostic walk-timeline scoring.

Both weather providers feed normalized per-hour data into ``walk_timeline``,
which returns the two strings (rain, temp) the firmware renders as the dog-
walk timeline. Pure computation — no network, no provider-specific shapes.
"""
from datetime import datetime

TIMELINE_HOURS = range(7, 21)  # 07h–20h, last segment = 20:00–21:00

# Rain thresholds — mm of precipitation in the hour:
#   < drizzle: clear (paper)
#   drizzle..rain: light raincoat fine (light gray)
#   >= rain: splash-proof overwhelmed; prepare or skip (mid gray)
RAIN_DRIZZLE_MM = 0.1
RAIN_RAIN_MM = 1.5

# Temperature comfort. Cold floor shifts seasonally (you dress for it); hot
# ceiling is constant (the dog doesn't care). "Extreme" = comfort ± offset.
EXTREME_OFFSET = 5
TEMP_HOT_COMFORT = 22
TEMP_COLD_COMFORT_BY_MONTH = {
    1:  -5,   2:  -5,   3:  2,    4:  5,    5:  11,   6:  12,
    7:  14,   8:  14,   9:  10,   10: 5,    11: 0,    12: -5,
}


def _rain_score(precip_mm: float) -> str:
    """0 = dry, 1 = drizzle, 2 = rain."""
    if precip_mm >= RAIN_RAIN_MM:
        return "2"
    if precip_mm >= RAIN_DRIZZLE_MM:
        return "1"
    return "0"


def _temp_score(temp_c: float, cold_comfort: float) -> str:
    """'0' comfortable, 'c'/'h' mild cold/hot, 'C'/'H' extreme cold/hot."""
    if temp_c > TEMP_HOT_COMFORT + EXTREME_OFFSET:
        return "H"
    if temp_c > TEMP_HOT_COMFORT:
        return "h"
    if temp_c < cold_comfort - EXTREME_OFFSET:
        return "C"
    if temp_c < cold_comfort:
        return "c"
    return "0"


def walk_timeline(rows: list[dict], date_str: str) -> dict[str, str] | None:
    """Compute the per-hour rain/temp score strings from normalized hourly data.

    ``rows`` is a list of per-hour dicts with at least ``h`` (int hour 0-23),
    ``precip`` (mm), and ``temp`` (°C). Hours outside TIMELINE_HOURS are
    ignored. Missing hours render as "0".

    Returns ``{"rain": "<14 chars>", "temp": "<14 chars>"}`` or ``None`` if no
    data overlapped the timeline window.
    """
    by_hour = {r["h"]: r for r in rows if r.get("h") in TIMELINE_HOURS}
    if not by_hour:
        return None

    month = datetime.strptime(date_str, "%Y-%m-%d").month
    cold_comfort = TEMP_COLD_COMFORT_BY_MONTH.get(month, 5)

    rain_out = []
    temp_out = []
    for hour in TIMELINE_HOURS:
        row = by_hour.get(hour)
        if row is None:
            rain_out.append("0")
            temp_out.append("0")
            continue
        rain_out.append(_rain_score(row.get("precip") or 0))
        temp_out.append(_temp_score(row.get("temp") if row.get("temp") is not None else 15,
                                     cold_comfort))
    return {"rain": "".join(rain_out), "temp": "".join(temp_out)}
