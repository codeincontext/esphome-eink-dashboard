# esphome-eink-dashboard

A dashboard for the Seeed [reTerminal E1003](https://www.seeedstudio.com/reTerminal-E1003-p-6391.html) e-ink display: weather, upcoming dates, and a thought of the day. The ESPHome firmware renders the display; a Python server assembles the content and publishes it via Home Assistant.

```
[ Python server ] → HA REST API → [ Home Assistant ] → [ ESPHome on reTerminal E1003 ]
   (Docker)            (entities)                          (e-ink render on wake)
```

## What's on the display

- **Weather** — per-day forecast with a per-hour walk timeline, COLD/HOT/WET overlays, "now" marker, and a short narrative paragraph for each day.
- **Upcoming dates** — birthdays, anniversaries, holidays from a YAML file. Items within 2 days highlighted.
- **Thought of the day** — quote, author, context paragraph; daily rotation.

## Hardware

- **Seeed reTerminal E1003** — 10.3" IT8951-driven e-paper, 1872×1404 grayscale, ESP32-S3, 3 hardware buttons, built-in battery.
- Wakes on schedule + button press; deep sleep otherwise.

## Firmware (ESPHome)

```bash
cd firmware/esphome
cp secrets.yaml.example secrets.yaml   # WiFi creds, API encryption key, OTA password
./flash-ota.sh                          # retries every 10s until device wakes
```

Helper functions (timeline rendering, gray palette, hour-label modes): [`firmware/esphome/reterminal_helpers.h`](firmware/esphome/reterminal_helpers.h).

## Server

Python service that polls data sources, generates narratives, and publishes to Home Assistant. Runs in Docker.

```bash
cd server
cp .env.example .env                            # HA + Meteoblue + LLM creds
cp data/dates.example.yml data/dates.yml        # add your own dates
docker compose up -d
```

### Weather data conventions

See [`NOTES.md`](NOTES.md) for AROME/ARPEGE precipitation timing (preceding-hour accumulation) and the snow-vs-rain downgrade for warm temperatures.

### Data sources

| Source | Config | Notes |
|---|---|---|
| Weather | `METEOBLUE_API_KEY`, `LAT`, `LON`, `TIMEZONE` in `.env` (`ASL` optional) | Open-Meteo + Meteoblue |
| Narratives | `ANTHROPIC_API_KEY` in `.env` | Context summaries |
| Dates | `data/dates.yml` | Recurring (DD-MM) or one-time (YYYY-MM-DD) |
| Holidays | Computed | Easter-relative dates |
| Thoughts | `data/thoughts.yml` | Quote + author + context, daily rotation |
