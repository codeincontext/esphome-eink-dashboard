"""Hybrid weather provider.

Always polls Open-Meteo (free) on every server tick to detect display-relevant
changes. Meteoblue is fetched only when:

- there's no cached MB data yet (first run after boot)
- today's timeline string flips a cell (the visible change signal)
- a daily morning cron tick has been reached (force-refresh for morning brief)
- a long staleness threshold has been exceeded (defensive safety net)

A daily call cap prevents runaway spend.

Open-Meteo is used purely as the change detector — its narratives are not
generated (we'd never display them; in hybrid mode the narrative comes from
the cached Meteoblue forecast, the data that's actually shown). If Meteoblue
has never succeeded, we fall back to serving Open-Meteo *with* narratives.
"""
import logging
from datetime import datetime, timedelta

from . import meteoblue, openmeteo

logger = logging.getLogger(__name__)

# Hybrid-mode safety: never let MB calls exceed this many per UTC day.
DAILY_MB_CAP = 10
# Force a refresh after this gap even if nothing else triggered it.
STALENESS = timedelta(hours=12)
# Daily morning cron: force a refresh at/after this local hour.
MORNING_CRON_HOUR = 6


class HybridProvider:
    def __init__(self) -> None:
        self.last_om_timelines: dict | None = None
        self.cached_mb_data: dict | None = None
        self.last_mb_fetch_at: datetime | None = None
        self.mb_call_log: list[datetime] = []
        self.last_morning_cron_at: datetime | None = None

    # ---- helpers ----------------------------------------------------------

    def _mb_calls_today(self) -> int:
        today = datetime.utcnow().date()
        self.mb_call_log = [t for t in self.mb_call_log if t.date() == today]
        return len(self.mb_call_log)

    def _morning_cron_due(self) -> bool:
        now = datetime.now()
        today_cron = now.replace(hour=MORNING_CRON_HOUR,
                                  minute=0, second=0, microsecond=0)
        if now < today_cron:
            return False
        return self.last_morning_cron_at is None or self.last_morning_cron_at < today_cron

    def _extract_timelines(self, forecast: dict | None) -> dict | None:
        if not forecast:
            return None
        out: dict[str, dict[str, str]] = {}
        for slot, day in (forecast.get("days") or {}).items():
            rain = day.get("timeline_rain")
            temp = day.get("timeline_temp")
            if rain is not None or temp is not None:
                out[slot] = {"rain": rain, "temp": temp}
        return out or None

    def _per_slot_diff(self, om_timelines: dict | None) -> dict[str, bool]:
        last = self.last_om_timelines
        diff: dict[str, bool] = {}
        if om_timelines is None:
            return diff
        for slot, val in om_timelines.items():
            diff[slot] = last is None or last.get(slot) != val
        return diff

    def _should_fetch_mb(self, om_timelines: dict | None) -> tuple[bool, str]:
        """Decide whether to call Meteoblue this tick. Returns (yes, reason).

        Only today's timeline drives event-based triggers. Tomorrow/day3 changes
        are deliberately ignored — they refresh via the morning cron + any
        future MB fetch (which returns all 3 days anyway). Empirically future
        days flip much more often than today, so any-day triggering quickly
        burned through the daily MB call budget without benefit on display.
        """
        if self._mb_calls_today() >= DAILY_MB_CAP:
            return False, "daily-cap-reached"
        if self.cached_mb_data is None:
            return True, "first-run"
        if self._morning_cron_due():
            return True, "morning-cron"
        if om_timelines is None:
            return False, "no-om-timelines"  # OM failed; just serve cache
        last_today = self.last_om_timelines.get("today") if self.last_om_timelines else None
        today = om_timelines.get("today")
        if last_today is None or today != last_today:
            return True, "today-changed"
        if self.last_mb_fetch_at is None or \
           datetime.utcnow() - self.last_mb_fetch_at > STALENESS:
            return True, "safety-net"
        return False, "no-change"

    # ---- main entry point -------------------------------------------------

    def get_weather(self) -> dict | None:
        # Open-Meteo is the change-detector. Skip its narratives — they'd just
        # thrash the single-entry LLM cache, and they're never shown in hybrid
        # mode unless MB has never succeeded.
        om = openmeteo.get_forecast()
        om_timelines = self._extract_timelines(om)

        diff = self._per_slot_diff(om_timelines)
        if diff:
            diff_str = " ".join(f"{slot}={'Y' if c else 'n'}" for slot, c in diff.items())
            print(f"hybrid: om-diff {diff_str}", flush=True)

        fetch, reason = self._should_fetch_mb(om_timelines)
        print(f"hybrid: {reason} (mb_calls_today={self._mb_calls_today()})", flush=True)

        if fetch:
            mb = meteoblue.get_weather()  # full pipeline — narratives included
            if mb is not None:
                self.cached_mb_data = mb
                self.last_mb_fetch_at = datetime.utcnow()
                self.mb_call_log.append(datetime.utcnow())
                if reason == "morning-cron":
                    self.last_morning_cron_at = datetime.now()

        # Update the timeline baseline so future ticks compare against the
        # latest OM state (even when MB wasn't fetched this tick).
        if om_timelines is not None:
            self.last_om_timelines = om_timelines

        if self.cached_mb_data is not None:
            return self.cached_mb_data

        # No MB data ever — fall back to OM with narratives one-off.
        if om is None:
            return None
        openmeteo.add_narratives(om)
        om.pop("_hourly_by_day", None)
        om.pop("_day_dates", None)
        return om


# Module-level singleton — the server's a long-running process so state
# persists across get_weather() calls.
_provider = HybridProvider()


def get_weather() -> dict | None:
    return _provider.get_weather()
