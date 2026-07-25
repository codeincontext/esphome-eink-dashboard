# Partial refresh & non-flashing waveforms on the reTerminal E1003

Working notes for migrating off the koosoli external component to ESPHome's
first-party `it8951` platform, to get partial refresh and fast (non-flashing)
waveforms — e.g. sliding the "now" arrow by refreshing only a sliver of the
panel instead of a full-screen GC16 flash.

## TL;DR

- **Current component (`it8951_reterminal_e1003`, koosoli) is a dead end for this.**
  Its source hardcodes whole-panel GC16 (`refresh_mode = 2`, full-area every draw).
  One config knob (`vcom`), no waveform selection, no partial windows. Open,
  unanswered issue asking for exactly this.
- **ESPHome core added a first-party `it8951` component** (PR #15346, merged
  2026-06-28, shipped in **2026.7.0**). It has a preconfigured
  `model: seeed-reterminal-e1003`, `update_mode` waveform selection, and
  partial refresh via `auto_clear_enabled: false`.
- **We're on ESPHome 2026.4.3 — must upgrade to ≥ 2026.7.0** to use it.
- **On Seeed panels only GC16 and DU are verified** to render correctly in the
  ESPHome component. The reduced-grayscale modes (GL16/GLR16/GLD16) and A2 can
  render a white background as grey. So realistically our palette is **GC16
  (full, flashing) + DU (fast, mono, non-flashing)**.

## Links

- Component docs: <https://esphome.io/components/display/it8951/>
- Release notes: <https://esphome.io/changelog/2026.7.0/>
- PR that added it: <https://github.com/esphome/esphome/pull/15346>
- Koosoli component (current, full-GC16-only): <https://github.com/koosoli/Seeed-10.3-inch-IT8951-ESPHome-Drivers>
- Its open partial-refresh issue: <https://github.com/koosoli/Seeed-10.3-inch-IT8951-ESPHome-Drivers/issues/1>

## The waveform modes (the "non-flashing options")

An e-paper "flash" is the black→white→black inversion a full waveform does to
reset every pixel to a clean state. The fast waveforms skip that inversion and
drive pixels directly toward their target — no flash, but they trade away grey
levels and/or accumulate **ghosting** (faint remnants of previous frames) that
eventually needs a full waveform to clear.

Timings below are relative/orders-of-magnitude — actual figures depend on the
panel size (ours is a large 10.3" 1872×1404) and temperature.

| Mode | Grey levels | Flash? | Speed | Ghosting | Verified on Seeed (ESPHome) | Notes |
|------|-------------|--------|-------|----------|:---------------------------:|-------|
| **INIT** | — | yes (clears to white) | slowest | clears all | init only | Used to blank / hard-reset the panel |
| **GC16** | 16 | **yes** (full inversion) | slow | clears | ✅ | Reference quality; our current everything |
| GL16 | 16 | light | medium | some | ❌ | Meant for sparse text on white |
| GLR16 | 16 | reduced | medium | reduced ("regal") | ❌ | GL16 + anti-ghost |
| GLD16 | 16 | reduced | medium | reduced | ❌ | GL16 + stronger anti-ghost |
| DU4 | 4 | **no** | fast | builds | ❌ | 4-level fast |
| **DU** | 2 (B/W) | **no** | fast | builds | ✅ | Direct mono update — the practical fast path |
| A2 | 2 (B/W) | **no** | fastest | minimal (per docs) | ❌ | Animation / page turns |

### Pros / cons of the ones that matter to us

**GC16 — full grayscale, flashes**
- Pro: all 16 grey levels, self-clearing (no ghost accumulation), the only mode
  that renders our grey walk-score cells correctly. Verified on this panel.
- Con: the visible black/white flash, and it's the slowest normal mode. Full
  panel every time on the current component.

**DU — 2-level mono, no flash**
- Pro: no flash, fast, verified on this panel. Ideal for black-on-white content
  that moves: the "now" triangle, the "Drawn HH:MM" timestamp, button-driven
  cursors.
- Con: **only black and white** — any grey in the refreshed region gets
  flattened to B/W. So DU is only safe over regions that are genuinely mono
  (white margin + black glyphs), *not* over the grey timeline cells. Ghosting
  builds, so you must periodically GC16 to clean up.

**A2 / DU4 / GL*16 — tempting but unverified here**
- A2 would be great for a smoothly sliding arrow (fastest, no flash) but it's
  **not verified on the Seeed panels** and may render white as grey — high risk
  of an ugly grey wash. Same caveat for the reduced-grayscale 16-level modes.
- Not worth it unless we test and confirm on the actual panel.

## Partial refresh — how the "only a sliver refreshes" works

In the official component, set **`auto_clear_enabled: false`** so the internal
framebuffer *persists* between updates instead of being wiped. Then each
`component.update` transfers and refreshes **only the bounding box of the pixels
the lambda actually drew**, not the whole panel. Horizontal extent is rounded
out to a multiple of 32 px (IT8951 hardware alignment); vertical extent is exact.

So to advance the arrow: redraw only the small strip it lives in. The refresh
region is the bounding box spanning its old and new x — a thin sliver. Pair that
sliver with **DU** and the arrow moves **silently and fast**, no full-screen
flash. The grey weather content underneath is untouched (still held in the
persistent framebuffer) so it doesn't need redrawing at all.

Because the arrow sits in the white margin *above* the coloured bar (see
`firmware/esphome/reterminal_helpers.h`, the "Now" marker "hovering above" the bar), the sliver
is genuinely mono → DU-safe. The lambda must **erase** the old arrow and paint
the new one (erasing counts as drawing and is included in the refreshed box); an
update that draws nothing is skipped entirely.

### The waveform is chosen per-update — no reverse-engineering needed

The docs confirm an **`it8951.update` action with a `mode:` override** (templatable):

```yaml
- it8951.update: { id: epaper_display, mode: DU }    # arrow sliver, silent
- it8951.update: { id: epaper_display, mode: GC16 }  # full weather render
```

So the GC16-full / DU-partial hybrid is directly expressible: we explicitly pick
the waveform per update rather than relying on the driver's automatic GC16/DU
selection (whose internal decision logic the docs don't spell out anyway).

## The target behaviour this unlocks

- **Arrow ticks silently** every minute (or few) via a DU partial sliver — no flash.
- **Full GC16 refresh only when weather data actually changes** (or on a periodic
  ghost-clear cadence, e.g. hourly / on the day↔night page switch).
- **Quiet nights**: quote page, no arrow, so no periodic refresh at all — only
  redraws if the quote changes.
- **Snappy buttons** while awake: page switches are full grey content so still
  GC16 (~a second or two), but no deep-sleep wake penalty.

Combined with dropping deep sleep (mains power), this is the "live but calm"
display we're after.

## Config reference (official `it8951`, from the docs)

Defaults that matter to us:

| Option | Default | Our value / note |
|--------|---------|------------------|
| `model` | (required) | `seeed-reterminal-e1003` (built-in preset) |
| `model` | (required) | `Seeed-reTerminal-E1003` — a **built-in preset** (one of 4: generic `IT8951`, `m5stack-m5paper`, `Seeed-reTerminal-E1003`, `Seeed-EE03`). Auto-sets pins + dimensions (cs/busy/reset/enable become optional). Still need the `spi:` block. |
| `vcom` | `2300` mV (generic fallback; no documented Seeed default) | **set `1400`.** This is *not* a copy-paste default — it's the value **Seeed documents for this exact panel**: their GxEPD2 driver comment says *"ED103TC2 needs VCOM = -1.40 V"* (they wrote a custom driver because it differs from the Waveshare 10.3"'s ~-2.33 V). So 1400 is a legitimate panel nominal. Residual uncertainty is only per-*unit* variance (tens of mV, marked on the FPC inside the case) — not worth chasing unless ghosting appears. |
| `update_mode` | `GC16` | default full; override per-update via the action |
| `auto_clear_enabled` | `true` | **set `false`** for persistent framebuffer → area updates |
| `full_update_every` | `30` | forced GC16 every N updates + first-after-boot, clears ghosting |
| `grayscale` | `true` | keep true for 16-level weather cells (see open Q) |
| `sleep_when_done` | `false` for Seeed | good — panel won't self-sleep between updates |
| `dithering` | `true` | only relevant in mono mode |
| `invert_colors` | `false` | — |
| `update_interval` | `1min` | set `never`; we drive updates manually |

## Results — implemented and verified on-panel (2026-07-25)

All the earlier uncertainty is resolved. The migration is done and running on the
device via ESPHome 2026.7.2 (isolated venv at `../.esphome-exp-venv`; the global
conda esphome was left untouched).

- **Can we mix GC16 and DU dynamically?** Yes — the `it8951.update` action takes a
  per-update `mode:` override. We drive full renders with `component.update` (GC16)
  and the arrow tick with `it8951.update: { mode: DU }`.
- **`auto_clear_enabled: false` gives bbox-only refresh?** Yes, confirmed on-panel.
- **Does DU render cleanly while `grayscale: true`?** **YES — verified on the
  physical panel.** The "now" arrow advances via a silent DU partial with no
  full-screen flash, crisp black-on-white, no grey wash. This was the pivotal
  unknown; it works. (Makes sense: the arrow band is pure black/white, so DU has
  no intermediate greys to mangle.)
- **Grayscale parity:** the 16-level walk-score timelines render the same under the
  official component's GC16 as under koosoli. Page navigation confirmed working.
- **Ghosting:** left `full_update_every` at its default (30); data-push GC16
  renders + the 07:00/21:00 page switches clear the band regularly. Revisit if
  ghosting ever looks objectionable during long data-stable stretches.

### What shipped (see `firmware/esphome/reterminal-e1003.yaml`)

- Display: `platform: it8951`, `model: Seeed-reTerminal-E1003`, `vcom: 1400`,
  `update_interval: never`, `auto_clear_enabled: false`.
- Always-on: deep sleep removed; `render_now` boot script; `on_time` triggers at
  07:00/21:00 switch page + full render; HA pushes → `debounced_redraw` (GC16).
- Silent arrow: `arrow_only` global gates a branch at the top of the `page_weather`
  lambda that erases + redraws only the arrow band; a 10-min `interval` fires
  `it8951.update: { mode: DU }` while the weather page is showing.

### Gotcha found in testing

The erase band must **not** reach `timeline_y` — the timeline's top border is drawn
at that row, and an over-tall erase wipes it (the arrow-only path doesn't redraw
the border, so it stays white until the next full render). Erase height is
`tri_h + gap`, stopping at the apex row `timeline_y - gap`, one clear row above the
border.

### Not yet done / follow-ups

- The `page_overview` arrow (manual Home press only) still updates on full render,
  not via the DU ticker — extend if desired.
- Touch is wired in hardware (GT911) but unused — see the touch ideas in chat.
- The koosoli config remains in git history as the fallback if the official path
  ever regresses.
