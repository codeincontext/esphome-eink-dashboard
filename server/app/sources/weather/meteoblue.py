"""Meteoblue weather provider.

Uses the ``basic-1h`` package — hourly forecasts including temperature,
precipitation, pictocode, and snowfraction. Daily aggregates (high/low,
dominant condition) are derived from the hourly data ourselves, which saves
~33% credits vs adding the ``basic-day`` package.

Returns the same dashboard-ready dict shape as the openmeteo provider so the
selector can use either interchangeably.
"""
import json
import logging
from collections import Counter
from datetime import datetime
from urllib.error import URLError
from urllib.request import Request, urlopen

from ... import config
from . import narrative
from .walk_score import walk_timeline

logger = logging.getLogger(__name__)

API_URL = "https://my.meteoblue.com/packages/basic-1h"

# Hourly window used both for the dominant-condition pick and the daily
# extremes. Matches what we'd see during waking hours on the dashboard.
DAYTIME_START = 7
DAYTIME_END = 21

# Hourly window included in the LLM narrative inputs (richer than the timeline
# so the model sees early-morning and late-evening transitions).
NARRATIVE_HOURS = range(6, 23)


# Meteoblue HOURLY pictocode → (icon glyph, normalized condition, human label).
# basic-1h returns the extended hourly set (1-35), not the daily summary set.
# Icon glyphs constrained to font_icon's allowlist (☀🌤⛅☁🌫🌦🌧🌨❄⛈).
PICTOCODE = {
    # Clear (1–6): sky with no significant cloud cover at low levels.
    1:  ("☀",  "clear",         "Clear"),
    2:  ("☀",  "clear",         "Clear"),
    3:  ("🌤", "clear",         "Clear with cirrus"),
    4:  ("🌤", "partly_cloudy", "Mostly sunny"),
    5:  ("🌤", "partly_cloudy", "Mostly sunny"),
    6:  ("🌤", "partly_cloudy", "Mostly sunny"),
    # Partly cloudy (7–9).
    7:  ("⛅", "partly_cloudy", "Partly cloudy"),
    8:  ("⛅", "partly_cloudy", "Partly cloudy"),
    9:  ("⛅", "partly_cloudy", "Partly cloudy"),
    # Convective build-up — clouds present but no precip; thunderstorms POSSIBLE.
    10: ("⛅", "partly_cloudy", "Building clouds"),
    11: ("⛅", "partly_cloudy", "Building clouds"),
    12: ("⛅", "partly_cloudy", "Building clouds"),
    # Hazy (13–15): visibility reduced but no real precipitation.
    13: ("🌤", "clear",         "Hazy"),
    14: ("🌤", "clear",         "Hazy"),
    15: ("🌤", "clear",         "Hazy"),
    # Fog / low stratus (16–18).
    16: ("🌫", "fog",            "Fog"),
    17: ("🌫", "fog",            "Fog"),
    18: ("🌫", "fog",            "Fog"),
    # Mostly cloudy to overcast (19–22).
    19: ("☁",  "overcast",      "Mostly cloudy"),
    20: ("☁",  "overcast",      "Mostly cloudy"),
    21: ("☁",  "overcast",      "Mostly cloudy"),
    22: ("☁",  "overcast",      "Overcast"),
    # Overcast with precipitation (23–26).
    23: ("🌧", "rain",           "Rain"),
    24: ("🌨", "snow",           "Snow"),
    25: ("🌧", "rain",           "Heavy rain"),
    26: ("🌨", "snow",           "Heavy snow"),
    # Thunderstorms (27–30).
    27: ("⛈", "thunder",        "Rain with thunder"),
    28: ("⛈", "thunder",        "Light rain with thunder"),
    29: ("⛈", "thunder",        "Snowstorm"),
    30: ("⛈", "thunder",        "Heavy rain with thunder"),
    # Mixed / showers (31–32).
    31: ("🌦", "showers",        "Mixed with showers"),
    32: ("🌨", "snow_showers",  "Mixed with snow showers"),
    # Light overcast precipitation (33–35).
    33: ("🌦", "drizzle",        "Light rain"),
    34: ("❄",  "snow",           "Light snow"),
    35: ("🌧", "rain_snow_mix", "Rain/snow mix"),
}

DEFAULT_ICON = "☁"
DEFAULT_CONDITION = "overcast"
DEFAULT_LABEL = "?"


def _fetch_api() -> dict | None:
    if not config.METEOBLUE_API_KEY or not config.LAT or not config.LON:
        logger.warning("Meteoblue: missing API key or location")
        return None
    params = {
        "apikey": config.METEOBLUE_API_KEY,
        "lat": config.LAT,
        "lon": config.LON,
        "format": "json",
        # Meteoblue defaults to UTC. Pass an explicit IANA tz so hourly times
        # match the local-hour interpretation the display uses. Their docs
        # say tz=auto is supported but in practice it falls back to GMT.
        "tz": config.TIMEZONE,
    }
    if config.ASL:
        params["asl"] = config.ASL
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{API_URL}?{qs}"
    try:
        req = Request(url, headers={"Accept": "application/json"})
        with urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
    except (URLError, json.JSONDecodeError, OSError) as e:
        logger.warning("Meteoblue fetch failed: %s", e)
        return None
    if data.get("error"):
        logger.warning("Meteoblue error: %s", data.get("error_message"))
        return None
    return data


def _build_hourly_rows(hourly: dict, date_str: str,
                        hours=NARRATIVE_HOURS) -> list[dict]:
    """Normalize Meteoblue hourly response into rows for the given date.

    Each row: ``{h, condition, precip, temp}``. Meteoblue's precipitation is
    already at the displayed hour (no preceding-hour shift like Open-Meteo),
    so no index offset is needed.
    """
    times = hourly.get("time", [])
    precip = hourly.get("precipitation", [])
    temp = hourly.get("temperature", [])
    picto = hourly.get("pictocode", [])

    rows: list[dict] = []
    for i, t in enumerate(times):
        if not t.startswith(date_str):
            continue
        try:
            hour = int(t[11:13])
        except (IndexError, ValueError):
            continue
        if hour not in hours:
            continue
        code = picto[i] if i < len(picto) else None
        condition = PICTOCODE.get(code, (DEFAULT_ICON, DEFAULT_CONDITION, DEFAULT_LABEL))[1]
        rows.append({
            "h": hour,
            "condition": condition,
            "precip": round(precip[i] if i < len(precip) else 0, 1),
            "temp": round(temp[i]) if i < len(temp) and temp[i] is not None else None,
        })
    return rows


def _daytime_extremes(hourly: dict, date_str: str) -> tuple[float | None, float | None]:
    """Min/max temperature during daytime hours for the given date."""
    times = hourly.get("time", [])
    temp = hourly.get("temperature", [])
    vals = []
    for i, t in enumerate(times):
        if not t.startswith(date_str):
            continue
        try:
            hour = int(t[11:13])
        except (IndexError, ValueError):
            continue
        if not (DAYTIME_START <= hour < DAYTIME_END):
            continue
        if i < len(temp) and temp[i] is not None:
            vals.append(temp[i])
    if not vals:
        return None, None
    return min(vals), max(vals)


def _dominant_pictocode(hourly: dict, date_str: str) -> int | None:
    """Most common pictocode during daytime hours for the given date."""
    times = hourly.get("time", [])
    picto = hourly.get("pictocode", [])
    counts: Counter = Counter()
    for i, t in enumerate(times):
        if not t.startswith(date_str):
            continue
        try:
            hour = int(t[11:13])
        except (IndexError, ValueError):
            continue
        if not (DAYTIME_START <= hour < DAYTIME_END):
            continue
        if i < len(picto) and picto[i] is not None:
            counts[int(picto[i])] += 1
    if not counts:
        return None
    return counts.most_common(1)[0][0]


def _format_body(label: str, condition: str, high: int, low: int, today: bool) -> str:
    body = f"{condition}, {high}°C" if today else f"{condition}, {high}/{low}°C"
    return f"{label}: {body}" if label else body


def _list_day_dates(hourly: dict, max_days: int = 3) -> list[str]:
    """Distinct date prefixes appearing in hourly times, in order, up to max_days."""
    seen: list[str] = []
    for t in hourly.get("time", []):
        date = t[:10]
        if date not in seen:
            seen.append(date)
            if len(seen) >= max_days:
                break
    return seen


def get_forecast() -> dict | None:
    """Fetch + assemble dashboard fields, but *without* LLM narratives.

    Returns a dict with ``days`` and the internal ``_hourly_by_day`` /
    ``_day_dates`` keys used by :func:`add_narratives`.
    """
    data = _fetch_api()
    if data is None:
        return None

    hourly = data.get("data_1h", {}) or {}
    day_dates = _list_day_dates(hourly)
    if not day_dates:
        return None

    slot_keys = ["today", "tomorrow", "day3"]
    days = {}
    hourly_by_day: dict[str, list[dict]] = {}

    for i, key in enumerate(slot_keys):
        if i >= len(day_dates):
            break
        date_str = day_dates[i]
        rows = _build_hourly_rows(hourly, date_str)
        hourly_by_day[key] = rows

        code = _dominant_pictocode(hourly, date_str)
        icon, _, label_text = PICTOCODE.get(
            code, (DEFAULT_ICON, DEFAULT_CONDITION, DEFAULT_LABEL)
        ) if code is not None else (DEFAULT_ICON, DEFAULT_CONDITION, DEFAULT_LABEL)

        dt = datetime.strptime(date_str, "%Y-%m-%d")
        label = "" if i == 0 else dt.strftime("%A")

        lo_raw, hi_raw = _daytime_extremes(hourly, date_str)
        high = round(hi_raw) if hi_raw is not None else 0
        low = round(lo_raw) if lo_raw is not None else 0
        body = _format_body("", label_text, high, low, today=(i == 0))

        day_data = {
            "label": label,
            "body": body,
            "icon": icon,
            "high": high,
            "low": low,
            "temp_low_part": f"{low}–",
            "temp_high_part": f"{high}°C",
        }
        timeline = walk_timeline(rows, date_str)
        if timeline:
            day_data["timeline_rain"] = timeline["rain"]
            day_data["timeline_temp"] = timeline["temp"]
        days[key] = day_data

    narrative_day_dates = [
        (k, day_dates[i]) for i, k in enumerate(slot_keys) if i < len(day_dates)
    ]
    return {"days": days, "_hourly_by_day": hourly_by_day, "_day_dates": narrative_day_dates}


def add_narratives(forecast: dict) -> None:
    """Read internal hourly data and overlay LLM narratives on the days dict."""
    hourly_by_day = forecast.get("_hourly_by_day")
    day_dates = forecast.get("_day_dates")
    if not hourly_by_day or not day_dates:
        return
    narratives = narrative.get_narratives(hourly_by_day, day_dates)
    if not narratives:
        return
    days = forecast.get("days", {})
    for slot, text in narratives.items():
        if slot in days:
            days[slot]["narrative"] = text


def get_weather() -> dict | None:
    """Full pipeline: fetch + narratives. Convenience for single-provider modes."""
    f = get_forecast()
    if f is None:
        return None
    add_narratives(f)
    f.pop("_hourly_by_day", None)
    f.pop("_day_dates", None)
    return f
