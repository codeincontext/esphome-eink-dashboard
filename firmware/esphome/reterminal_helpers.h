// Shared helpers for the reTerminal E1003 dashboard lambdas.
// Included into the build via esphome: includes: in the YAML, so each page's
// lambda can reuse the wrap functions, the gray palette and the timeline
// fill-colour mapping without duplicating code.

#pragma once

#include "esphome.h"

#include <initializer_list>
#include <string>
#include <utility>

namespace reterminal {

// Which hour ticks (e.g. "14h") get rendered under a timeline.
//   NONE:   no labels
//   ALL:    label every hour from 7h..21h (used on the full today timeline)
//   SPARSE: just the bookends and creche markers — 7h, 9h, 16h, 21h (mini)
enum class HourLabelMode { NONE, ALL, SPARSE };

// 16-level grayscale matching the IT8951's GC16 waveform — gray(0) is full
// ink (black), gray(15) is paper (white). Each step is ~17 RGB units.
inline esphome::Color gray(int level) {
  int v = (255 * level) / 15;
  return esphome::Color(v, v, v);
}

// Semantic aliases for layout intent.
inline const esphome::Color WHITE       = gray(15);
inline const esphome::Color BLACK       = gray(0);
inline const esphome::Color GRAY_BG     = gray(13);
inline const esphome::Color PARA_MUTED  = gray(2);
inline const esphome::Color TITLE_MUTED = gray(4);
inline const esphome::Color FOOTER      = gray(4);

// Returns the line count the text would occupy if wrapped to max_width,
// and writes the pixel width of the last line into *last_w (if not null).
inline int count_wrapped_lines(esphome::display::Display &it,
                                esphome::display::BaseFont *font,
                                int max_width, std::string text,
                                int *last_w = nullptr) {
  int lines = 0;
  int last_line_w = 0;
  while (!text.empty()) {
    size_t end = text.length();
    int line_w = 0;
    while (end > 0) {
      int x1, y1, w, h;
      it.get_text_bounds(0, 0, text.substr(0, end).c_str(), font,
                         esphome::display::TextAlign::TOP_LEFT,
                         &x1, &y1, &w, &h);
      if (w <= max_width) {
        line_w = w;
        break;
      }
      size_t last_space = text.rfind(' ', end - 1);
      if (last_space == std::string::npos || last_space == 0) break;
      end = last_space;
    }
    if (end == 0) {
      end = text.length();
      int x1, y1, w, h;
      it.get_text_bounds(0, 0, text.substr(0, end).c_str(), font,
                         esphome::display::TextAlign::TOP_LEFT,
                         &x1, &y1, &w, &h);
      line_w = w;
    }
    lines++;
    last_line_w = line_w;
    if (end >= text.length()) break;
    text = text.substr(end);
    if (!text.empty() && text[0] == ' ') text = text.substr(1);
  }
  if (last_w) *last_w = last_line_w;
  return lines;
}

// Greedy word-wrap renderer. Background colour is passed to print() so
// bpp:4 fonts antialias against the actual background rather than ESPHome's
// assumed-black backdrop.
inline void print_wrapped(esphome::display::Display &it,
                           int x, int y, esphome::display::BaseFont *font,
                           esphome::Color color, esphome::Color bg,
                           int max_width, int line_height,
                           std::string text) {
  int cy = y;
  while (!text.empty()) {
    size_t end = text.length();
    while (end > 0) {
      int x1, y1, w, h;
      it.get_text_bounds(0, 0, text.substr(0, end).c_str(), font,
                         esphome::display::TextAlign::TOP_LEFT,
                         &x1, &y1, &w, &h);
      if (w <= max_width) break;
      size_t last_space = text.rfind(' ', end - 1);
      if (last_space == std::string::npos || last_space == 0) break;
      end = last_space;
    }
    if (end == 0) end = text.length();
    it.print(x, cy, font, color, text.substr(0, end).c_str(), bg);
    cy += line_height;
    if (end >= text.length()) break;
    text = text.substr(end);
    if (!text.empty() && text[0] == ' ') text = text.substr(1);
  }
}

// Walk-timeline cell colour for a per-hour score char. Rain and temp use
// distinct char sets so they can be shaded independently — rain caps at
// mid-gray (no near-black) so dark cells unambiguously mean "extreme temp".
//   '0'         → clear / comfort  (paper)
//   '1'         → drizzle          (light gray)
//   '2'         → rain             (mid gray)
//   'c','h'     → mild temp        (mid gray)
//   'C','H'     → extreme temp     (near black)
inline esphome::Color timeline_fill_for(char c) {
  switch (c) {
    case '0': return gray(15);
    case '1': case 'c': case 'h': return gray(11);
    case '2': return gray(3);
    case 'C': case 'H': default: return gray(2);
  }
}

// Render a timeline row with soft (lerp'd) boundaries between cells of
// different colors. Cells of the same color blend into each other naturally
// since both sides of the ramp are equal.
inline void draw_timeline_row(esphome::display::Display &it,
                               const std::string &scores,
                               int bar_x, int row_top, int seg_w, int row_h,
                               int feather = 5) {
  int n = (int)scores.length();
  int total_w = n * seg_w;
  for (int x = 0; x < total_w; x++) {
    int cell_idx = x / seg_w;
    if (cell_idx >= n) break;
    int x_in_cell = x - cell_idx * seg_w;
    esphome::Color c = timeline_fill_for(scores[cell_idx]);
    esphome::Color final_c = c;
    if (cell_idx > 0 && x_in_cell < feather) {
      esphome::Color left_c = timeline_fill_for(scores[cell_idx - 1]);
      if (left_c.r != c.r) {
        float t = 0.5f + (x_in_cell + 0.5f) / (2.0f * feather);
        int v = (int)(left_c.r + (c.r - left_c.r) * t);
        final_c = esphome::Color(v, v, v);
      }
    } else if (cell_idx < n - 1 && x_in_cell >= seg_w - feather) {
      esphome::Color right_c = timeline_fill_for(scores[cell_idx + 1]);
      if (right_c.r != c.r) {
        int d = x_in_cell - (seg_w - feather);
        float t = (d + 0.5f) / (2.0f * feather);
        int v = (int)(c.r + (right_c.r - c.r) * t);
        final_c = esphome::Color(v, v, v);
      }
    }
    it.filled_rectangle(bar_x + x, row_top, 1, row_h, final_c);
  }
}

// Walk away the runs of identical chars in `scores`, drawing the icon for
// any matching run (white-on-black-bg) at the centre of the run. Used to
// label why a stretch of cells is dark on the timeline (rain / cold / hot).
inline void draw_icons_in_runs(
    esphome::display::Display &it,
    const std::string &scores,
    int bar_x, int seg_w, int row_top, int row_h,
    esphome::display::BaseFont *icon_font,
    const std::initializer_list<std::pair<char, const char*>> &mapping,
    int text_y_offset = 0) {
  int i = 0;
  while (i < (int)scores.length()) {
    char ch = scores[i];
    const char *icon = nullptr;
    for (auto &p : mapping)
      if (p.first == ch) { icon = p.second; break; }
    if (icon == nullptr) { i++; continue; }
    int j = i;
    while (j < (int)scores.length() && scores[j] == ch) j++;
    int center_x = bar_x + ((i + j) * seg_w) / 2;
    int center_y = row_top + row_h / 2 + text_y_offset;
    esphome::Color cell_bg = timeline_fill_for(ch);
    it.print(center_x, center_y, icon_font, WHITE,
             esphome::display::TextAlign::CENTER, icon, cell_bg);
    i = j;
  }
}

}  // namespace reterminal
