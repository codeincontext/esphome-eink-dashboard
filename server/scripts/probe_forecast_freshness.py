#!/usr/bin/env python3
"""Probe when Open-Meteo (and later Meteoblue) ingest new AROME / ARPEGE runs.

Polls the forecast endpoints every N minutes, hashes the content (excluding
volatile metadata like ``generationtime_ms``), and writes a CSV row each time
the hash *changes* — those moments are when a new model run became available
through the API.

Usage::

    cd server
    python -u scripts/probe_forecast_freshness.py --interval 10 --duration 36

Args:
    --interval MIN    Poll cadence in minutes (default 10).
    --duration HOURS  How long to run (default 24).
    --out PATH        Output CSV path (default scripts/freshness.csv).

The script reads LAT, LON, METEOBLUE_API_KEY from environment if available
(typically the same .env we use for the main server) — load with::

    env $(cat .env | xargs) python -u scripts/probe_forecast_freshness.py ...
"""
import argparse
import csv
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.error import URLError
from urllib.request import Request, urlopen


OPENMETEO_URL = "https://api.open-meteo.com/v1/meteofrance"
PRIMARY_MODEL = "meteofrance_arome_france_hd"
FALLBACK_MODEL = "meteofrance_arpege_europe"

METEOBLUE_URL = "https://my.meteoblue.com/packages/basic-1h"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _hash_payload(payload: dict, exclude_keys: tuple[str, ...]) -> str:
    """Hash the payload after removing volatile metadata fields."""
    pruned = {k: v for k, v in payload.items() if k not in exclude_keys}
    return hashlib.sha256(
        json.dumps(pruned, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]


# Dashboard-relevant slice: only the hours we'd actually render. Hashing this
# subset means "did the visible content change?" — distant-future or off-screen
# nowcasting tweaks don't count as a change.
DISPLAY_HOURS = range(7, 22)  # 7h..21h inclusive on the timeline
DISPLAY_DAYS = 3


def _hash_dashboard(rows: list[tuple]) -> str:
    """Hash a normalized list of (date, hour, *display_values) tuples."""
    return hashlib.sha256(
        json.dumps(rows, sort_keys=True, default=str).encode()
    ).hexdigest()[:16]


def _extract_openmeteo_display_rows(data: dict) -> list[tuple]:
    """For each (date, hour ∈ DISPLAY_HOURS, day < DISPLAY_DAYS):
    (date, hour, precip, temp, code) using primary model, falling back to alt.
    """
    hourly = data.get("hourly", {}) or {}
    times = hourly.get("time", [])
    precip_p = hourly.get(f"precipitation_{PRIMARY_MODEL}", [])
    precip_f = hourly.get(f"precipitation_{FALLBACK_MODEL}", [])
    temp_p = hourly.get(f"temperature_2m_{PRIMARY_MODEL}", [])
    temp_f = hourly.get(f"temperature_2m_{FALLBACK_MODEL}", [])
    code_p = hourly.get(f"weather_code_{PRIMARY_MODEL}", [])
    code_f = hourly.get(f"weather_code_{FALLBACK_MODEL}", [])

    def pick(arr_p, arr_f, i):
        if i < len(arr_p) and arr_p[i] is not None:
            return arr_p[i]
        if i < len(arr_f) and arr_f[i] is not None:
            return arr_f[i]
        return None

    days_seen: list[str] = []
    rows: list[tuple] = []
    for i, t in enumerate(times):
        date = t[:10]
        if date not in days_seen:
            if len(days_seen) >= DISPLAY_DAYS:
                break
            days_seen.append(date)
        hour = int(t[11:13])
        if hour not in DISPLAY_HOURS:
            continue
        rows.append((
            date, hour,
            pick(precip_p, precip_f, i),
            pick(temp_p, temp_f, i),
            pick(code_p, code_f, i),
        ))
    return rows


def _extract_meteoblue_display_rows(data: dict) -> list[tuple]:
    """For each (date, hour ∈ DISPLAY_HOURS, day < DISPLAY_DAYS):
    (date, hour, precip, temp, pictocode, snowfraction).
    """
    hourly = data.get("data_1h", {}) or {}
    times = hourly.get("time", [])
    precip = hourly.get("precipitation", [])
    temp = hourly.get("temperature", [])
    picto = hourly.get("pictocode", [])
    snowf = hourly.get("snowfraction", [])

    days_seen: list[str] = []
    rows: list[tuple] = []
    for i, t in enumerate(times):
        # Meteoblue's time format is "YYYY-MM-DD HH:MM"
        date = t[:10]
        if date not in days_seen:
            if len(days_seen) >= DISPLAY_DAYS:
                break
            days_seen.append(date)
        try:
            hour = int(t[11:13])
        except (IndexError, ValueError):
            continue
        if hour not in DISPLAY_HOURS:
            continue
        def at(arr, idx):
            return arr[idx] if idx < len(arr) else None
        rows.append((
            date, hour,
            at(precip, i),
            at(temp, i),
            at(picto, i),
            at(snowf, i),
        ))
    return rows


def probe_meteoblue(lat: str, lon: str, apikey: str, asl: str | None = None) -> dict | None:
    """Return ``{hash, gen_ms, hourly_first_time, raw}`` or None on failure.

    Costs 8000 Meteoblue credits per call. Skip if Package API isn't enabled
    on the key — the response will say so and we exit cleanly.
    """
    params = {
        "apikey": apikey,
        "lat": lat,
        "lon": lon,
        "format": "json",
    }
    if asl:
        params["asl"] = asl
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{METEOBLUE_URL}?{qs}"
    try:
        with urlopen(Request(url, headers={"Accept": "application/json"}),
                     timeout=15) as r:
            data = json.loads(r.read())
    except (URLError, json.JSONDecodeError, OSError) as e:
        print(f"  meteoblue fetch failed: {e}", file=sys.stderr)
        return None
    if data.get("error"):
        print(f"  meteoblue error: {data.get('error_message')}", file=sys.stderr)
        return None
    metadata = data.get("metadata", {}) or {}
    rows = _extract_meteoblue_display_rows(data)
    h = _hash_dashboard(rows)
    return {
        "hash": h,
        "gen_ms": metadata.get("generation_time_ms"),
        "hourly_first_time": metadata.get("modelrun_utc"),
    }


def probe_openmeteo(lat: str, lon: str) -> dict | None:
    """Return ``{hash, gen_ms, hourly_first_time, raw}`` or None on failure."""
    params = {
        "latitude": lat,
        "longitude": lon,
        "models": f"{PRIMARY_MODEL},{FALLBACK_MODEL}",
        "hourly": "temperature_2m,precipitation,weather_code",
        "forecast_days": 3,
        "timezone": "auto",
    }
    qs = "&".join(f"{k}={v}" for k, v in params.items())
    url = f"{OPENMETEO_URL}?{qs}"
    try:
        with urlopen(Request(url, headers={"Accept": "application/json"}),
                     timeout=15) as r:
            data = json.loads(r.read())
    except (URLError, json.JSONDecodeError, OSError) as e:
        print(f"  openmeteo fetch failed: {e}", file=sys.stderr)
        return None
    rows = _extract_openmeteo_display_rows(data)
    h = _hash_dashboard(rows)
    hourly = data.get("hourly", {})
    first_t = (hourly.get("time") or [None])[0]
    return {
        "hash": h,
        "gen_ms": data.get("generationtime_ms"),
        "hourly_first_time": first_t,
    }


AROME_RUN_HOURS_UTC = (0, 3, 6, 9, 12, 15, 18, 21)


def next_arome_run_utc(now: datetime | None = None) -> datetime:
    """Return the next AROME run boundary in UTC, strictly after ``now``."""
    if now is None:
        now = _now_utc()
    base = now.replace(minute=0, second=0, microsecond=0)
    aligned = base.replace(hour=(base.hour // 3) * 3)
    for offset in range(0, 25, 3):
        cand = aligned + timedelta(hours=offset)
        if cand > now and cand.hour in AROME_RUN_HOURS_UTC:
            return cand
    return aligned  # unreachable in practice


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--interval", type=int, default=10, help="open-meteo poll cadence (min)")
    p.add_argument("--meteoblue", action="store_true", help="also poll Meteoblue (8k credits/call)")
    p.add_argument("--meteoblue-interval", type=int, default=30,
                    help="meteoblue poll cadence (min); coarser to save credits")
    p.add_argument("--duration", type=float, default=24, help="run for N hours")
    p.add_argument("--out", default="scripts/freshness.csv")
    p.add_argument("--no-wait", action="store_true",
                    help="don't wait for the next AROME run boundary before starting")
    p.add_argument("--stop-after", type=int, default=0,
                    help="stop after N change events have been recorded (0=run for full duration)")
    args = p.parse_args()

    if not args.no_wait:
        target = next_arome_run_utc()
        wait_s = (target - _now_utc()).total_seconds()
        print(f"Waiting until {target.isoformat()} (next AROME run, "
              f"in {int(wait_s // 60)}m {int(wait_s % 60)}s)…", flush=True)
        time.sleep(max(0, wait_s))

    lat = os.environ.get("LAT")
    lon = os.environ.get("LON")
    if not lat or not lon:
        print("LAT/LON env vars required", file=sys.stderr)
        sys.exit(1)

    out_path = args.out
    fresh_file = not os.path.exists(out_path)
    out = open(out_path, "a", newline="")
    w = csv.writer(out)
    if fresh_file:
        w.writerow(["poll_utc", "provider", "hash", "changed",
                     "gen_ms", "hourly_first_time"])

    apikey = os.environ.get("METEOBLUE_API_KEY")
    asl = os.environ.get("ASL")  # optional; defaults to Meteoblue auto-detect
    if args.meteoblue and not apikey:
        print("METEOBLUE_API_KEY required when --meteoblue is set", file=sys.stderr)
        sys.exit(1)

    deadline = time.time() + args.duration * 3600
    interval_s = args.interval * 60
    mb_interval_s = args.meteoblue_interval * 60
    prev_hash: dict[str, str] = {}
    next_mb_poll = time.time() if args.meteoblue else None
    changes_seen = 0

    def record(provider: str, r: dict) -> str:
        nonlocal changes_seen
        prev = prev_hash.get(provider)
        if prev is None:
            changed = "init"
        elif prev != r["hash"]:
            changed = "Y"
            changes_seen += 1
        else:
            changed = "n"
        ts = _now_utc().isoformat(timespec="seconds")
        w.writerow([ts, provider, r["hash"], changed, r["gen_ms"], r["hourly_first_time"]])
        out.flush()
        marker = "  *CHANGED*" if changed == "Y" else ""
        print(f"{ts}  {provider:10} hash={r['hash']}{marker}", flush=True)
        prev_hash[provider] = r["hash"]
        return changed

    poll_summary = f"openmeteo every {args.interval} min"
    if args.meteoblue:
        poll_summary += f" + meteoblue every {args.meteoblue_interval} min"
    print(f"Probing {poll_summary} for {args.duration} h → {out_path}", flush=True)

    while time.time() < deadline:
        r = probe_openmeteo(lat, lon)
        if r is not None:
            record("openmeteo", r)
        if args.meteoblue and next_mb_poll is not None and time.time() >= next_mb_poll:
            r_mb = probe_meteoblue(lat, lon, apikey, asl)
            if r_mb is not None:
                record("meteoblue", r_mb)
            next_mb_poll += mb_interval_s
        if args.stop_after and changes_seen >= args.stop_after:
            print(f"Reached {changes_seen} change events — stopping.", flush=True)
            break
        time.sleep(interval_s)

    out.close()
    print("Done.")


if __name__ == "__main__":
    main()
