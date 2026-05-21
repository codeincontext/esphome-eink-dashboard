# esphome-eink-dashboard

A daily-brief dashboard for the Seeed [reTerminal E1003](https://www.seeedstudio.com/reTerminal-E1003-p-6391.html) e-ink display: weather, upcoming dates, and a quote of the day. The ESPHome firmware renders the display; a Python server assembles the content and publishes it via Home Assistant.

```
[ Python server ] → HA REST API → [ Home Assistant ] → [ ESPHome on reTerminal E1003 ]
   (Docker)            (entities)                          (e-ink render on wake)
```

## What's on the display

- **Weather** — hybrid forecast: [Open-Meteo](https://open-meteo.com/) (free, AROME) polled every 5 min as a change-detector, [Meteoblue](https://www.meteoblue.com/) (paid, ~12k credits/call) called only when today's timeline flips or once each morning. Per-hour walk-score timeline with COLD/HOT/WET overlays, "now" marker, narratives via Claude.
- **Upcoming dates** — birthdays, anniversaries, holidays from a YAML file. Items within 2 days highlighted.
- **Thought of the day** — quote + author + context paragraph, daily rotation.

## Hardware

- **Seeed reTerminal E1003** — 10.3" IT8951-driven e-paper, 1872×1404 grayscale, ESP32-S3, 3 hardware buttons, built-in battery.
- Wakes on schedule + button press (deep sleep otherwise).
- Subscribes to HA entities via ESPHome's `homeassistant:` text_sensor platform.

## Firmware (ESPHome)

```bash
cd firmware/esphome
cp secrets.yaml.example secrets.yaml   # WiFi creds, API key, OTA password
./flash-ota.sh                          # retries every 10s until device wakes
```

Layout reference: [`firmware/esphome/LAYOUT.md`](firmware/esphome/LAYOUT.md). Helper functions (timeline rendering, gray palette, hour-label modes): [`firmware/esphome/reterminal_helpers.h`](firmware/esphome/reterminal_helpers.h).

## Server

Python service that polls data sources, generates narratives via Claude, and publishes a flat set of entities to Home Assistant. Runs in Docker.

```bash
cd server
cp .env.example .env                            # MQTT/HA + Meteoblue + Anthropic creds
cp data/dates.example.yml data/dates.yml        # add your own dates
docker compose up -d
```

The image is published to `ghcr.io/codeincontext/esphome-eink-dashboard:latest` by GitHub Actions on each push to `main` that touches `server/**` — `docker compose pull` always grabs the latest build.

### Weather data conventions

See [`NOTES.md`](NOTES.md) for AROME/ARPEGE precipitation timing (preceding-hour accumulation) and the snow-downgrade rule for warm temperatures.

### Data sources

| Source | Config | Notes |
|---|---|---|
| Weather | `METEOBLUE_API_KEY`, `LAT`, `LON`, `ASL`, `TIMEZONE` in `.env` | Hybrid Open-Meteo + Meteoblue |
| Narratives | `ANTHROPIC_API_KEY` in `.env` | Claude-generated context summaries |
| Dates | `data/dates.yml` | Recurring (DD-MM) or one-time (YYYY-MM-DD) |
| Holidays | Computed | Easter-relative dates |
| Thoughts | `data/thoughts.yml` | Quote + author + context, daily rotation |

## Related

- [`waveshare-12in48-gfx`](https://github.com/codeincontext/waveshare-12in48-gfx) — original Adafruit GFX-based firmware for the Waveshare 12.48" tri-color display, on a stock ESP32. Prototype that preceded this project.
