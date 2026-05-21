"""LLM-generated weather narratives.

Provider-agnostic: callers pass normalized hourly data (a list of rows per
slot, each row {h, condition, precip, temp}). Open-Meteo and Meteoblue map
their respective codes to the same ``condition`` enum before invoking us.

Memoised by hash of the input data so identical hourly forecasts always
produce the same narrative — avoids spurious e-ink refreshes from non-
deterministic LLM output.
"""
import hashlib
import json
import logging
from datetime import datetime

from anthropic import Anthropic, APIError

from ... import config

logger = logging.getLogger(__name__)

MODEL = "claude-haiku-4-5-20251001"
NARRATIVE_HOURS = range(6, 23)

# Allowed values for the ``condition`` field in input rows. Both providers
# normalize to this set; the prompt explains them to the model.
CONDITIONS = {
    "clear", "partly_cloudy", "overcast", "fog",
    "drizzle", "rain", "showers",
    "snow", "snow_showers", "rain_snow_mix",
    "thunder",
}

# Single-entry memo: {input_hash: {today, tomorrow, day3}}
_cache: dict[str, dict[str, str]] = {}


def _input_hash(inputs: dict) -> str:
    return hashlib.sha256(json.dumps(inputs, sort_keys=True, default=str).encode()).hexdigest()


PROMPT_HEADER = """You write short weather summaries for a personal e-ink dashboard for a French alpine valley (~1060m). Given hourly forecast data for three days, write ONE concise sentence per day capturing the temporal pattern (when rain starts/stops, peak warmth or chill, fog clearing, etc.). 10–15 words per sentence. Plain prose, no emojis, no markdown.

The dashboard already shows the daytime min/max temperature for each day in a separate widget, so the narrative can lean on qualitative description (e.g. "a chilly morning warming through the afternoon") rather than restating exact degree values.

Condition labels: clear, partly_cloudy, overcast, fog, drizzle, rain, showers, snow, snow_showers, rain_snow_mix, thunder.

Hourly data (h=hour, cond=condition, precip=mm, temp=°C):
"""


def _format_prompt(day_labels: dict[str, str],
                    hourly_by_day: dict[str, list[dict]],
                    current_hour: int) -> str:
    parts = [PROMPT_HEADER]
    parts.append(
        f"Current local hour: {current_hour:02d}h. For 'today', the user has "
        f"already lived through earlier hours — lean the sentence toward "
        f"what's still ahead."
    )
    for slot, rows in hourly_by_day.items():
        parts.append(f"\n{day_labels.get(slot, slot)}:")
        for r in rows:
            parts.append(
                f"  {r['h']:02d}h cond={r.get('condition', '?')} "
                f"precip={r.get('precip', 0)} temp={r.get('temp')}"
            )
    parts.append(
        '\n\nReturn JSON only, no prose: '
        '{"today": "...", "tomorrow": "...", "day3": "..."}'
    )
    return "\n".join(parts)


def get_narratives(
    hourly_by_day: dict[str, list[dict]],
    day_dates: list[tuple[str, str]],
    now: datetime | None = None,
) -> dict[str, str] | None:
    """Return {today, tomorrow, day3} narratives, or None on any failure.

    Args:
        hourly_by_day: normalized hourly data per slot, e.g.
            ``{"today": [{"h": 7, "condition": "clear", "precip": 0, "temp": 8}, ...]}``.
            Each row is a dict with at least ``h``, ``condition``, ``precip``, ``temp``.
            Hours outside NARRATIVE_HOURS may be filtered by the provider.
        day_dates: ``[("today", "2026-05-04"), ("tomorrow", "2026-05-05"), ...]``
            — used to label each day in the prompt and order the slots.
        now: defaults to datetime.now(); current hour is included in the cache
            hash so narratives rotate hourly even when forecasts haven't changed.
    """
    if not config.ANTHROPIC_API_KEY:
        return None

    if now is None:
        now = datetime.now()
    current_hour = now.hour

    h = _input_hash({"hour": current_hour, "inputs": hourly_by_day})
    if h in _cache:
        return _cache[h]

    day_labels: dict[str, str] = {}
    for slot, date_str in day_dates:
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            day_labels[slot] = f"{slot.capitalize()} ({dt.strftime('%A %B %-d')})"
        except ValueError:
            day_labels[slot] = slot

    prompt = _format_prompt(day_labels, hourly_by_day, current_hour)

    try:
        client = Anthropic(api_key=config.ANTHROPIC_API_KEY)
        msg = client.messages.create(
            model=MODEL,
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        text = msg.content[0].text.strip()
        # Strip code fences if the model added them.
        if text.startswith("```"):
            text = text.split("```", 2)[1]
            if text.startswith("json"):
                text = text[4:]
            text = text.strip()
        result = json.loads(text)
    except (APIError, json.JSONDecodeError, IndexError, KeyError) as e:
        logger.warning("Narrative generation failed: %s", e)
        return None

    _cache.clear()
    _cache[h] = result
    return result
