# Notes

## Weather data conventions (Open-Meteo / AROME)

Open-Meteo passes through Météo-France AROME's native conventions:

- **Precipitation** at timestamp `T(N):00` = sum of rain accumulated **over the preceding hour** (i.e. `(N-1):00 → N:00`). Same convention applies to ARPEGE and most operational NWP models.
- **Temperature, weather_code** at `T(N):00` = instantaneous value at exactly `N:00`.
- AROME France HD runs every 3h (8× daily) at 1.3km resolution and 15-min internal time steps; we get hourly aggregates.

**Implication for the walk timeline**: each cell labelled `Nh` visually represents the period `N:00 → (N+1):00`. To make the precipitation in cell `Nh` reflect rain expected *during* that period (not the previous hour), we look up precip at index `i+1` in `_walk_timeline`. Temperature stays at index `i` because it's instantaneous. The "now" triangle marker is positioned by fractional hour, so at 18:30 it sits in the middle of the `18h` cell.

## Meteoblue API gotchas

- Package URL form: `/packages/basic-1h_basic-day` (joined with underscore). `basic-1h` = 8,000 credits, `basic-day` = 4,000. **Combined cost: 12,000 credits per call.**
- The "Package API" capability must be **enabled on the API key** in the Meteoblue account settings. If it's off, package URLs return "Available credits exceeded" (misleading — the actual cause is the capability being disabled, not the credit balance).
- Pass `tz=Europe/Paris` (or whatever the local IANA zone is) explicitly. Their docs say `tz=auto` is supported but in practice it falls back to UTC, which shifts hourly cell indices.
- **Hybrid call budget**: hard cap of 10 MB calls/day in `hybrid.py`. Calling-pattern history once exhausted a yearly allowance — `DAILY_MB_CAP` exists as a defence against that recurring.

### ASL (elevation) — explicit vs auto-resolved

When `asl` is omitted, Meteoblue resolves elevation from terrain data (DEM) at the lat/lon. In flat terrain that's fine; in steep terrain it can be off by hundreds of metres because of grid resolution, which would shift temperature and the snow-vs-rain line.

**Test result (2026-05-22, single run, May)**: at our production coords MB auto-resolved 1001 m vs our explicit 1060 m (59 m gap). Zero hourly temperature or snowfraction differences exceeded thresholds (0.3 °C / 0.1). Verdict: ASL not worth setting at these coords. Caveat: tested in May with no snow in the forecast, so the snow-line case isn't exercised. Re-run in winter before concluding.

Probe script (gitignored, local-only): `server/scripts/probe_asl_comparison.py`.

## OTA flashing

The reTerminal is on a separate VLAN from the dev machine. mDNS resolution works cross-VLAN provided the router has a mDNS proxy enabled (UniFi calls it "Gateway mDNS Proxy"). The OTA service runs on TCP 3232; if you're on a routed-zone setup you'll also need a firewall rule allowing the dev subnet to reach the device on that port.

`firmware/esphome/flash-ota.sh` wraps `esphome run` in a retry loop with 10s sleep — useful because the device is deep-sleep most of the time and the upload only succeeds during a wake window. Hostname is preferred over the raw IP since the IP could change.

USB serial (`/dev/cu.wchusbserial110` on this machine) still works as a fallback.

## Future: weather provider — Météo-France direct

Currently using Open-Meteo's `/v1/meteofrance` endpoint with `meteofrance_arome_france_hd` + `meteofrance_arpege_europe`. Underlying model data is Météo-France's, but the **daily aggregation** is Open-Meteo's: it picks the most-significant hourly weather code, which produces "Light rain" for a day with 23h overcast and 1h trace drizzle.

Workaround in place: derive the dominant hourly weather code ourselves during waking hours.

Why Météo-France direct may still be worth it later:
- Their daily summary is human-tuned / climatology-aware
- Fewer pessimistic mismatches between condition and precipitation
- We're already targeting their data — going through one fewer hop

Costs to plan for:
- Register on portail-api.meteofrance.fr (token-based auth, French portal)
- Different condition codes — own pictocodes, not WMO. New icon mapping table needed.
- Endpoint paths and JSON shape to learn
- Still need Open-Meteo for the hourly walk-window logic, OR refactor to use Météo-France for hourly too
- Two providers running side-by-side during transition (treat the new one as `weather_alt` first, compare for a week, then promote)

## Future: 13.3" Spectra 6 full-colour e-ink

7 colours (black, white, red, yellow, blue, green, orange), 1600x1200. Slower refresh (~30s+) but much richer output for a daily status board.

- Pimoroni Inky Impression 13.3": https://shop.pimoroni.com/products/inky-impression?variant=55186435277179
- Waveshare 13.3" e-Paper HAT+: https://www.waveshare.com/13.3inch-e-paper-hat-plus-e.htm
