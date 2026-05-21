import json
import logging
from datetime import datetime
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from ... import config
from . import narrative
from .walk_score import walk_timeline, TIMELINE_HOURS  # noqa: F401

# WMO → normalized condition string used by the LLM narrative.
_WMO_TO_CONDITION = {
    0: "clear", 1: "clear", 2: "partly_cloudy", 3: "overcast",
    45: "fog", 48: "fog",
    51: "drizzle", 53: "drizzle", 55: "drizzle",
    56: "drizzle", 57: "drizzle",
    61: "rain", 63: "rain", 65: "rain",
    66: "rain", 67: "rain",
    71: "snow", 73: "snow", 75: "snow", 77: "snow",
    80: "showers", 81: "showers", 82: "showers",
    85: "snow_showers", 86: "snow_showers",
    95: "thunder", 96: "thunder", 99: "thunder",
}

logger = logging.getLogger(__name__)

# WMO weather interpretation codes → (icon, label)
WMO_CODE = {
    0: ("☀", "Clear"),
    1: ("🌤", "Mainly clear"),
    2: ("⛅", "Partly cloudy"),
    3: ("☁", "Overcast"),
    45: ("🌫", "Fog"),
    48: ("🌫", "Fog"),
    51: ("🌦", "Light drizzle"),
    53: ("🌦", "Drizzle"),
    55: ("🌧", "Heavy drizzle"),
    56: ("🌧", "Freezing drizzle"),
    57: ("🌧", "Freezing drizzle"),
    61: ("🌦", "Light rain"),
    63: ("🌧", "Rain"),
    65: ("🌧", "Heavy rain"),
    66: ("🌧", "Freezing rain"),
    67: ("🌧", "Freezing rain"),
    71: ("🌨", "Light snow"),
    73: ("❄", "Snow"),
    75: ("❄", "Heavy snow"),
    77: ("🌨", "Snow grains"),
    80: ("🌦", "Light showers"),
    81: ("🌧", "Showers"),
    82: ("🌧", "Heavy showers"),
    85: ("🌨", "Snow showers"),
    86: ("❄", "Heavy snow showers"),
    95: ("⛈", "Thunderstorm"),
    96: ("⛈", "Thunderstorm"),
    99: ("⛈", "Thunderstorm"),
}

WALK_START = 8
WALK_END = 18
TEMP_WINDOW_START = 9  # window for computing daily high/low
TEMP_WINDOW_END = 22
HOT_THRESHOLD = 24  # °C — above this, prefer cool periods for walks

API_URL = "https://api.open-meteo.com/v1/meteofrance"
PRIMARY_MODEL = "meteofrance_arome_france_hd"
FALLBACK_MODEL = "meteofrance_arpege_europe"


def _fetch_api() -> dict | None:
    params = {
        "latitude": config.LAT,
        "longitude": config.LON,
        "models": f"{PRIMARY_MODEL},{FALLBACK_MODEL}",
        "daily": ",".join([
            "weather_code",
            "temperature_2m_max",
            "temperature_2m_min",
            "precipitation_sum",
            "precipitation_probability_max",
            "snowfall_sum",
        ]),
        "hourly": "temperature_2m,precipitation,weather_code",
        "forecast_days": 3,
        # Use explicit TIMEZONE (same as Meteoblue) for consistency. OM's
        # "auto" works too, but pinning prevents drift if their auto-detect
        # ever differs.
        "timezone": config.TIMEZONE,
    }
    url = f"{API_URL}?{urlencode(params)}"
    try:
        req = Request(url, headers={"Accept": "application/json"})
        with urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except (URLError, json.JSONDecodeError, OSError) as e:
        logger.warning("Open-Meteo fetch failed: %s", e)
        return None


def _pick(daily: dict, var: str, i: int):
    """Get value at index i, preferring AROME HD, falling back to ARPEGE."""
    for model in (PRIMARY_MODEL, FALLBACK_MODEL):
        series = daily.get(f"{var}_{model}", [])
        if i < len(series) and series[i] is not None:
            return series[i]
    return None


def _walk_summary(hourly: dict) -> str | None:
    times = hourly.get("time", [])
    precip = hourly.get(f"precipitation_{PRIMARY_MODEL}", [])
    temps = hourly.get(f"temperature_2m_{PRIMARY_MODEL}", [])

    today = datetime.now().strftime("%Y-%m-%d")
    hours = []
    for i, t in enumerate(times):
        if not t.startswith(today):
            continue
        hour = int(t[11:13])
        if WALK_START <= hour < WALK_END:
            hours.append({
                "hour": hour,
                "precip": (precip[i] if i < len(precip) else 0) or 0,
                "temp": (temps[i] if i < len(temps) else 0) or 0,
            })

    if not hours:
        return None

    dry_windows = []
    window_start = None
    window_end = None
    for h in hours:
        if h["precip"] == 0:
            if window_start is None:
                window_start = h
            window_end = h
        else:
            if window_start is not None:
                dry_windows.append((window_start, window_end))
                window_start = None
    if window_start is not None:
        dry_windows.append((window_start, window_end))

    if not dry_windows:
        return "No dry windows today"

    all_dry = (
        len(dry_windows) == 1
        and dry_windows[0][0]["hour"] == WALK_START
        and dry_windows[0][1]["hour"] == WALK_END - 1
    )
    day_peak = max(h["temp"] for h in hours)
    hot = day_peak > HOT_THRESHOLD
    extreme_label = "coolest" if hot else "warmest"
    extreme_fn = min if hot else max

    if all_dry:
        pick = extreme_fn(hours, key=lambda h: h["temp"])
        return f"Dry all day, {extreme_label} around {pick['hour']}h ({round(pick['temp'])}°C)"

    best = max(dry_windows, key=lambda w: w[1]["hour"] - w[0]["hour"])
    start_h = best[0]["hour"]
    end_h = best[1]["hour"] + 1
    window_temp = extreme_fn(h["temp"] for h in hours if start_h <= h["hour"] <= best[1]["hour"])
    return f"Dry {start_h}h–{end_h}h ({round(window_temp)}°C)"


TIMELINE_HOURS = range(7, 21)  # 07h–20h, last segment = 20:00–21:00
RAIN_LIGHT_MM = 0.1      # below = clear; at/above = splash-proof raincoat needed
RAIN_HEAVY_MM = 1.5      # at/above = splash-proof overwhelmed; prepare or skip
EXTREME_OFFSET = 5       # °C — extreme = comfort ± this

# Hot side — about the dog. Constant year-round; Pixel doesn't dress down for summer.
TEMP_HOT_COMFORT = 22

# Cold side — about the human. Shifts seasonally because you dress for the weather.
TEMP_COLD_COMFORT_BY_MONTH = {
    1:  -5,   # Jan
    2:  -5,   # Feb
    3:  2,    # Mar
    4:  5,    # Apr
    5:  11,   # May
    6:  12,   # Jun
    7:  14,   # Jul
    8:  14,   # Aug
    9:  10,   # Sep
    10: 5,    # Oct
    11: 0,    # Nov
    12: -5,   # Dec
}


def _rain_score(precip_mm: float) -> int:
    """0 = dry, 1 = light rain, 2 = heavy rain."""
    if precip_mm >= RAIN_HEAVY_MM:
        return 2
    if precip_mm >= RAIN_LIGHT_MM:
        return 1
    return 0


def _temp_score(temp_c: float, cold_comfort: float) -> str:
    """Direction-aware temp score:
        '0' = comfortable
        'c' = mildly cold, 'C' = extreme cold
        'h' = mildly hot,  'H' = extreme hot
    """
    if temp_c > TEMP_HOT_COMFORT + EXTREME_OFFSET:
        return "H"
    if temp_c > TEMP_HOT_COMFORT:
        return "h"
    if temp_c < cold_comfort - EXTREME_OFFSET:
        return "C"
    if temp_c < cold_comfort:
        return "c"
    return "0"


NARRATIVE_HOURS = range(6, 23)  # used for both narrative + as the "wide" hourly window


def _build_hourly_rows(hourly: dict, date_str: str, hours=NARRATIVE_HOURS) -> list[dict]:
    """Convert Open-Meteo hourly response into normalized rows for the given
    date, restricted to ``hours``. Each row: ``{h, condition, precip, temp}``.

    Applies Open-Meteo's preceding-hour precipitation shift (precip at T(H+1)
    = rain accumulated during H:00→(H+1):00), the phantom-wet downgrade, and
    the implausible-snow downgrade.
    """
    times = hourly.get("time", [])
    code_p = hourly.get(f"weather_code_{PRIMARY_MODEL}", [])
    code_f = hourly.get(f"weather_code_{FALLBACK_MODEL}", [])
    precip_p = hourly.get(f"precipitation_{PRIMARY_MODEL}", [])
    precip_f = hourly.get(f"precipitation_{FALLBACK_MODEL}", [])
    temp_p = hourly.get(f"temperature_2m_{PRIMARY_MODEL}", [])
    temp_f = hourly.get(f"temperature_2m_{FALLBACK_MODEL}", [])

    def pick(arr_p, arr_f, i):
        if i < len(arr_p) and arr_p[i] is not None:
            return arr_p[i]
        if i < len(arr_f) and arr_f[i] is not None:
            return arr_f[i]
        return None

    rows: list[dict] = []
    for i, t in enumerate(times):
        if not t.startswith(date_str):
            continue
        hour = int(t[11:13])
        if hour not in hours:
            continue
        precip = pick(precip_p, precip_f, i + 1) or 0
        temp = pick(temp_p, temp_f, i)
        code = pick(code_p, code_f, i)
        code_int = int(code) if code is not None else None
        if code_int in _WET_CODES and precip <= 0:
            code_int = 3
        code_int = _downgrade_implausible_snow(code_int, temp)
        rows.append({
            "h": hour,
            "condition": _WMO_TO_CONDITION.get(code_int, "overcast"),
            "precip": round(precip, 1),
            "temp": round(temp) if temp is not None else None,
        })
    return rows


# WMO codes that imply precipitation (drizzle/rain/snow/showers/thunder).
# If AROME's hourly precipitation says 0 for an hour with one of these codes,
# we treat the code as "overcast" (3) instead — the high-res precip is ground
# truth, the code is sometimes a coarser-model artifact.
_WET_CODES = frozenset({51, 53, 55, 56, 57, 61, 63, 65, 66, 67,
                         71, 73, 75, 77, 80, 81, 82, 85, 86, 95, 96, 99})

# AROME sometimes emits a snow code at our valley elevation (~1060m) when the
# precip is actually rain at ground level — the model code may reflect snow
# aloft, or snow on adjacent higher terrain inside the same 1.3km grid cell.
# If the 2m temperature is well above freezing, downgrade snow codes to their
# rain equivalents at our location.
SNOW_TEMP_THRESHOLD_C = 3
_SNOW_TO_RAIN = {
    71: 61, 73: 63, 75: 65,  # light/moderate/heavy snow → equivalent rain
    77: 51,                   # snow grains → light drizzle
    85: 80, 86: 82,           # snow showers → rain showers
}


def _downgrade_implausible_snow(code: int | None, temp_c: float | None) -> int | None:
    """If a snow code coincides with a 2m temp clearly above freezing at our
    elevation, replace it with the rain-equivalent code. Returns the (possibly
    modified) code, or the original value if no change applies."""
    if code is None or temp_c is None:
        return code
    if code in _SNOW_TO_RAIN and temp_c > SNOW_TEMP_THRESHOLD_C:
        return _SNOW_TO_RAIN[code]
    return code


def _dominant_code(hourly: dict, date_str: str) -> int | None:
    """Most-common weather code during the TEMP_WINDOW for the given date.

    Open-Meteo's daily weather_code picks the most-significant hourly code,
    so a single hour of trace drizzle dominates 23 hours of overcast.
    AROME doesn't provide weather_code (only ARPEGE does), so we use AROME's
    high-res precipitation to override "wet" codes that have no actual rain.
    """
    times = hourly.get("time", [])
    code_p = hourly.get(f"weather_code_{PRIMARY_MODEL}", [])
    code_f = hourly.get(f"weather_code_{FALLBACK_MODEL}", [])
    precip_p = hourly.get(f"precipitation_{PRIMARY_MODEL}", [])
    precip_f = hourly.get(f"precipitation_{FALLBACK_MODEL}", [])
    temp_p = hourly.get(f"temperature_2m_{PRIMARY_MODEL}", [])
    temp_f = hourly.get(f"temperature_2m_{FALLBACK_MODEL}", [])

    counts: dict[int, int] = {}
    for i, t in enumerate(times):
        if not t.startswith(date_str):
            continue
        hour = int(t[11:13])
        if not (TEMP_WINDOW_START <= hour < TEMP_WINDOW_END):
            continue
        code = code_p[i] if i < len(code_p) and code_p[i] is not None else None
        if code is None:
            code = code_f[i] if i < len(code_f) and code_f[i] is not None else None
        if code is None:
            continue
        code = int(code)
        precip = precip_p[i] if i < len(precip_p) and precip_p[i] is not None else None
        if precip is None:
            precip = precip_f[i] if i < len(precip_f) and precip_f[i] is not None else 0
        temp = temp_p[i] if i < len(temp_p) and temp_p[i] is not None else None
        if temp is None:
            temp = temp_f[i] if i < len(temp_f) and temp_f[i] is not None else None
        if code in _WET_CODES and not (precip and precip > 0):
            code = 3  # downgrade phantom wet codes to overcast
        code = _downgrade_implausible_snow(code, temp)
        counts[code] = counts.get(code, 0) + 1

    if not counts:
        return None
    return max(counts, key=counts.get)


def _daytime_extremes(hourly: dict, date_str: str) -> tuple[float | None, float | None]:
    """Min and max temp for the given date, restricted to TEMP_WINDOW hours."""
    times = hourly.get("time", [])
    primary = hourly.get(f"temperature_2m_{PRIMARY_MODEL}", [])
    fallback = hourly.get(f"temperature_2m_{FALLBACK_MODEL}", [])
    temps = []
    for i, t in enumerate(times):
        if not t.startswith(date_str):
            continue
        hour = int(t[11:13])
        if not (TEMP_WINDOW_START <= hour < TEMP_WINDOW_END):
            continue
        v = primary[i] if i < len(primary) and primary[i] is not None else None
        if v is None:
            v = fallback[i] if i < len(fallback) and fallback[i] is not None else None
        if v is not None:
            temps.append(v)
    if not temps:
        return None, None
    return max(temps), min(temps)


def _format_body(icon: str, condition: str, high: int, low: int, today: bool = False) -> tuple[str, str]:
    """Return (main, aux) for the day. main is the headline part rendered in
    full ink; aux is the secondary parenthetical (e.g. "(min 10)") meant to be
    rendered in a muted colour. aux is empty when there's nothing secondary."""
    prefix = f"{icon} " if icon else ""
    if today:
        return f"{prefix}{condition}, {high}°C", f"(min {low})"
    return f"{prefix}{condition}, {high}/{low}°C", ""


def _format_summary(label: str, body: str) -> str:
    return f"{label}: {body}" if label else body


def _format_precip(rain_mm: float, snow_cm: float, prob) -> str:
    parts = []
    if snow_cm > 0:
        parts.append(f"{round(snow_cm)}cm snow")
    if rain_mm > 0:
        parts.append(f"{round(rain_mm)}mm rain")
    if not parts:
        return ""
    text = " + ".join(parts)
    if prob:
        text += f" ({prob}%)"
    return text


def get_forecast() -> dict | None:
    """Fetch + assemble dashboard fields, but *without* LLM narratives.

    Returns a dict with ``days`` (display fields) and a hidden ``_hourly_by_day``
    used by :func:`add_narratives`. Callers can pop ``_hourly_by_day`` before
    handing off if they don't intend to add narratives.
    """
    if not config.LAT or not config.LON:
        return None

    data = _fetch_api()
    if data is None:
        return None

    daily = data.get("daily", {})
    times = daily.get("time", [])
    if not times:
        return None

    slot_keys = ["today", "tomorrow", "day3"]
    days = {}
    hourly_by_day: dict[str, list[dict]] = {}
    hourly = data.get("hourly", {})
    for i, key in enumerate(slot_keys):
        if i >= len(times):
            break
        rows = _build_hourly_rows(hourly, times[i])
        hourly_by_day[key] = rows

        code = _dominant_code(hourly, times[i])
        if code is None:
            code = _pick(daily, "weather_code", i)
        icon, condition = WMO_CODE.get(int(code), ("", "?")) if code is not None else ("", "?")
        dt = datetime.strptime(times[i], "%Y-%m-%d")
        label = "" if i == 0 else dt.strftime("%A")
        hi_raw, lo_raw = _daytime_extremes(hourly, times[i])
        high = round(hi_raw) if hi_raw is not None else round(_pick(daily, "temperature_2m_max", i) or 0)
        low = round(lo_raw) if lo_raw is not None else round(_pick(daily, "temperature_2m_min", i) or 0)
        is_today = i == 0
        body, _ = _format_body("", condition, high, low, today=is_today)
        day_data = {
            "label": label,
            "body": body,
            "icon": icon,
            "high": high,
            "low": low,
            "temp_low_part": f"{low}–",
            "temp_high_part": f"{high}°C",
        }
        timeline = walk_timeline(rows, times[i])
        if timeline:
            day_data["timeline_rain"] = timeline["rain"]
            day_data["timeline_temp"] = timeline["temp"]
        days[key] = day_data

    # _day_dates is needed by add_narratives, stash alongside the hourly rows
    day_dates = [(k, times[i]) for i, k in enumerate(slot_keys) if i < len(times)]
    return {"days": days, "_hourly_by_day": hourly_by_day, "_day_dates": day_dates}


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
