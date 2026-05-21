# Daily-brief layout — reTerminal E1003

Spec for the ESPHome lambda. Coordinates use the device's native landscape orientation
(1872 × 1404). Origin is top-left.

## Bands (top to bottom)

| Band | y-start | y-end | Height | Background |
|---|---|---|---|---|
| Header | 0 | 80 | 80 | Black fill |
| Weather | 80 | 430 | 350 | None (white) |
| Quote | 430 | 700 | 270 | Light gray fill |
| Context | 700 | 780 | 80 | None (white) |

Y values are reference proportions taken from the SensCraft mock; on the device's
full 1404-px height we either scale by ~1.8 to fill the screen or keep these and
leave whitespace at the bottom. Lock in once the first lambda is rendering.

40px outer left/right padding throughout.

## Header band (y 0–80)

```
┌────────────────────────────────────────────────────────────┐
│ [date, top-left]                          [footer, top-right]
└────────────────────────────────────────────────────────────┘
```

| Widget | Anchor | Position | Width | Style |
|---|---|---|---|---|
| `date` | top-left | x=40, y=20 (baseline) | auto | bold sans 36pt, white on black |
| `footer` | top-right | x=1832, y=20 (baseline) | auto, right-aligned | sans 18pt, gray (#888) on black |

Entities: `sensor.eink_date`, `sensor.eink_footer`.

## Weather band (y 80–430)

Two columns side-by-side. Left column = today (60% width), right column = other
days (40% width). Column split at x ≈ 1100.

```
┌────────────────────────────────────┬──────────────────────┐
│ [icon, top-left]                   │ [tomorrow summary]   │
│                                    │                      │
│                                    │ [day3 summary]       │
│                                    │                      │
│ [today body, bottom-anchored]      │                      │
│ [walk, below body]                 │                      │
└────────────────────────────────────┴──────────────────────┘
```

### Left column — today (x 40–1080)

| Widget | Anchor | Position | Style |
|---|---|---|---|
| `weather.today.icon` | top-left | x=60, y=130 | sans 80pt |
| `weather.today.body` | bottom of column | x=60, y=370 (baseline) | bold sans 28pt |
| `walk` | below body | x=60, y=410 (baseline) | italic sans 22pt, gray (#666) |

Icon sits in the upper portion. Body + walk are anchored to the band's **bottom**
so they line up with the right column's bottom row regardless of icon size.

Entities: `sensor.eink_weather_today_icon`, `sensor.eink_weather_today_body`,
`sensor.eink_walk`.

### Right column — other days (x 1100–1832)

| Widget | Anchor | Position | Style |
|---|---|---|---|
| `weather.tomorrow.summary` | bottom of column, line 2 from bottom | x=1100, y=350 (baseline) | sans 22pt |
| `weather.day3.summary` | bottom of column, line 1 from bottom | x=1100, y=400 (baseline) | sans 22pt |

Both share the same x-start, line-height = 50px. Width = 732px (1832−1100), so
summaries up to ~50 chars fit at 22pt without wrapping.

Entities: `sensor.eink_weather_tomorrow_summary`, `sensor.eink_weather_day3_summary`.

## Quote band (y 430–700)

```
┌────────────────────────────────────────────────────────────┐
│                                                            │
│   [quote text, two lines, left-aligned, deep padding]      │
│                                                            │
│                          [author, italic, bottom-right]    │
└────────────────────────────────────────────────────────────┘
```

Background fill: light gray (#EEEEEE) covering the entire band.

| Widget | Anchor | Position | Width | Style |
|---|---|---|---|---|
| `thought.text` | top-left, padded | x=80, y=480 (baseline of line 1) | 1712px (1832−120) | bold serif 52pt, line-height 70px |
| `thought.author` | bottom-right | x=1832, y=670 (baseline) | auto, right-aligned | italic serif 28pt |

Quote anchored ~50px from band top; author anchored ~30px from band bottom. If the
quote is short, there's empty space between them — that's intentional.

Entities: `sensor.eink_thought_text`, `sensor.eink_thought_author`.

## Context band (y 700–780)

```
┌────────────────────────────────────────────────────────────┐
│ [context text, small, full-width, two lines max]           │
└────────────────────────────────────────────────────────────┘
```

| Widget | Anchor | Position | Width | Style |
|---|---|---|---|---|
| `thought.context` | top-left, padded | x=40, y=720 (baseline) | 1792px (1832−40) | sans 18pt, line-height 28px |

Entity: `sensor.eink_thought_context`.

## Anchoring summary

- **Header**: both widgets top-anchored.
- **Weather left col**: icon top-anchored; body + walk **bottom-anchored** to align with right col.
- **Weather right col**: both widgets bottom-anchored from band bottom upward — day3 closest to bottom.
- **Quote**: text top-anchored, author bottom-right-anchored — independent vertical positioning.
- **Context**: top-anchored.

This means the layout doesn't shift when individual texts vary in length, as long
as each widget's box is wide enough to hold its content without wrapping. Wrapping
is *expected* only for `thought.text` (2-line quote at 52pt) and `thought.context`
(2-line small text).

## Currently exposed but not rendered

These fields are in the HA payload but the SensCraft layout doesn't use them.
Drop in v1 of the ESPHome layout, or add later:

- `weather.*.narrative` — LLM-generated sentences (could replace `body` for prose)
- `weather.*.precip_summary` — rain/snow text, only useful when non-empty
- `upcoming.*` — 5 slots for upcoming dates; currently empty in the screenshot
