# Render progress output — Design (short)

**Date:** 2026-07-06
**Status:** Approved; implemented inline (small change).

## Problem

During a real render the pipeline is silent — every FFmpeg/HUD step runs with
captured output — so the user can't tell it's still working (segment mode with the
HUD renders hundreds of 4K frames per clip, taking minutes).

## Solution

Add lightweight progress output to **stderr** (stdout stays clean; `--dry-run`
returns before rendering so it is unaffected):

1. **Info dump after analysis** (in `main.py`, before the segment render): print the
   selected clips — count, and per clip `start→end  name  [stats]` (same content as
   the `--dry-run` listing).
2. **Per-clip progress** (in `video_editor.build_segment_reel`):
   - `[i/n] <name> (<dur>s) — HUD-frames renderen…`
   - a `\r`-refreshed `frames X/Y (Z%)` line during the HUD PNG loop (real %, since
     that loop is in Python), then `[i/n] <name> — overlay encoderen…` before the
     (subprocess, uncounted) FFmpeg overlay encode.
   - fallback/lower-third path prints `[i/n] <name> — lower-third…`.
3. **Flow mode** (`build_highlight_reel`): per-segment `[i/n] segment a-bs knippen…`.
4. **Concat/music** (`_concat_and_music`): `Segmenten samenvoegen…` and, when music
   is given, `Muziek mixen…`.
5. **Final assembly** (`main.py`): `Intro + eindmontage renderen…` before
   `build_final_video`.

FFmpeg encode steps show a status line only (not a %); the per-frame % covers the
bulk of the HUD time. No new dependency.

## Testing

The 66 existing tests must stay green (progress prints don't change return values or
outputs). Verified by a real short segment render showing the progress lines.

## Out of scope

Parsing FFmpeg `-progress` for a true encode % (user chose the status-line approach).
