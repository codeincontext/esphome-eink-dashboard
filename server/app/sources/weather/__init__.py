"""Weather provider selector.

Dispatches to the configured provider's ``get_weather()`` implementation. All
providers return the same dashboard-ready dict shape:

    {
        "days": {
            "today":    {label, body, icon, high, low,
                         temp_low_part, temp_high_part,
                         narrative, timeline_rain, timeline_temp, ...},
            "tomorrow": {...},
            "day3":     {...},
        }
    }

Configure via the ``WEATHER_PROVIDER`` env var. Defaults to ``openmeteo``.
"""
import logging
import os

from . import openmeteo

logger = logging.getLogger(__name__)

PROVIDER = os.environ.get("WEATHER_PROVIDER", "openmeteo").lower()


def get_weather() -> dict | None:
    if PROVIDER == "openmeteo":
        return openmeteo.get_weather()
    if PROVIDER == "meteoblue":
        from . import meteoblue
        return meteoblue.get_weather()
    if PROVIDER == "hybrid":
        from . import hybrid
        return hybrid.get_weather()
    logger.warning("Unknown WEATHER_PROVIDER %r — falling back to openmeteo", PROVIDER)
    return openmeteo.get_weather()
