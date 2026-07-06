# Sync debug + auto-align — Design

**Date:** 2026-07-06
**Status:** Approved for planning
**Context:** Follow-up to ride-highlight-editor. Real footage revealed that video
`creation_time` is unreliable after copying, so the metadata-derived sync offset
is wrong and almost the entire clip (including a ~10s standstill at the start) is
kept. We need a way to diagnose the alignment and correct the offset without
trusting the file timestamp.

## Goal

Add a diagnosis mode and an offset-correction mechanism so a user can (1) see how
the pipeline aligns a video against the GPX, and (2) fix a wrong offset — either
manually or via automatic motion/speed cross-correlation.

## Scope

- Single video per run (unchanged). Multi-clip-in-one-run is explicitly out of
  scope and left as a future follow-up; the user runs the tool per clip.
- Telemetry stays GPX-only.

## Core idea

The pipeline already computes optical-flow motion per video-second. GPS speed per
second comes from the GPX. When correctly aligned, these two time-series correlate
(standstill ↔ low speed, action ↔ high speed). Sliding the video's motion signal
over the GPS speed signal and taking the lag of maximum correlation yields the
true offset, independent of the (broken) file timestamp.

## CLI flags (main.py)

- `--inspect` — diagnosis mode: renders nothing, prints the debug report below.
- `--sync-offset SECONDS` — force a manual offset (float; may be negative).
- `--auto-sync` — use the cross-correlation estimate as the offset for a real render.

**Offset precedence when resolving the offset used for detection/render:**
1. `--sync-offset` if provided.
2. else the cross-correlation estimate if `--auto-sync` is set.
3. else the `creation_time`/mtime offset (current default), keeping the existing
   "no creation_time metadata → mtime" warning.

`offset_seconds` is defined exactly as today: video-time `t` maps to activity-time
`t + offset_seconds`.

## Debug report (`--inspect`, terminal)

1. **GPX summary:** activity start (UTC + local), duration, total distance,
   elevation gain, moving time, trackpoint count.
2. **Video:** `creation_time` value + source (`metadata` or `mtime`), duration, fps.
3. **Sync comparison:** metadata offset, auto-aligned offset + correlation score
   (confidence in [-1, 1]), and which offset is selected (per precedence).
4. **Aligned ASCII sparklines**, plotted on **video time with the chosen offset
   applied** (matches what the final render cuts), bucketed into ~100 columns:
   - GPS speed (km/h) at `speed_at_video_time(t, offset)`
   - Video motion (optical flow per second)
   - Per-second interest score
   Start/end time labels and min/max legends per row so peaks can be compared by eye.

## New / changed code

`highlight_detector.py`:
- `estimate_offset_by_motion(flow_per_sec, gpx, cfg) -> tuple[float, float]` —
  returns `(offset_seconds, correlation)`. Builds the GPS speed series (per
  activity-second), normalizes both signals, and for each candidate integer lag
  in a plausible range computes Pearson correlation of `flow[t]` vs
  `gps_speed[t + lag]` over the overlapping window; returns the lag with the
  highest correlation and that correlation value. Guards against degenerate
  (zero-variance) windows.
- `@dataclass Analysis` with: `flow_per_sec: list[float]`,
  `speeds_at_video: list[float]` (for the chosen offset), `scores: list[float]`,
  `segments: list[Segment]`, `offset_used: float`, `offset_source: str`
  (`"manual"|"auto"|"metadata"|"mtime"`), `metadata_offset: float`,
  `auto_offset: float`, `auto_correlation: float`, `video_creation_time`,
  `from_metadata: bool`, `video_duration: float`, `fps: float`.
- `analyze_video(video_path, gpx, cfg, offset_override=None, use_auto=False) ->
  Analysis` — computes optical flow **once**, computes the metadata offset and the
  auto estimate, resolves the offset via the precedence above, scores and merges,
  and returns the full `Analysis`. This is the single place flow is computed.
- `detect_highlights(video_path, gpx, cfg) -> tuple[list[Segment], list[float]]`
  becomes a thin wrapper over `analyze_video` (existing behavior/signature
  preserved so current callers and tests are unaffected).

`main.py`:
- Add `--inspect`, `--sync-offset`, `--auto-sync`.
- `resolve_offset_args(args) -> tuple[float | None, bool]` → `(offset_override,
  use_auto)` passed into `analyze_video`.
- `print_inspection(analysis, gpx)` — renders the report and sparklines.
- Normal render path calls `analyze_video(...)` (with the resolved offset args)
  instead of `detect_highlights`, so `--sync-offset`/`--auto-sync` affect renders.

`config.py`:
- Add `sparkline_columns: int = 100` (report width). No other config needed; the
  auto-align search range is derived from the signal lengths.

## Sparkline rendering

A pure helper `render_sparkline(values: list[float], width: int) -> str` maps a
series into `▁▂▃▄▅▆▇█` blocks: bucket the series into `width` columns (mean per
bucket), normalize min→`▁`/max→`█`; an empty or flat series renders as a row of
`▁`. Lives in main.py (presentation) and is unit-tested.

## Error handling

- Weak/zero-variance correlation: `estimate_offset_by_motion` returns the best lag
  it found with its (possibly low) correlation; `--inspect` displays the
  correlation so the user can judge confidence. `--auto-sync` still uses the best
  estimate but the low score is visible in `--inspect`.
- `--sync-offset` and `--auto-sync` together: `--sync-offset` wins (documented).
- Missing `creation_time`: unchanged mtime fallback + warning; `offset_source`
  reports `"mtime"`.

## Testing (pytest, in test_sync.py)

- `estimate_offset_by_motion` recovers a known lag: build a GPS speed series and a
  flow series that is a shifted, correlated copy; assert the estimated offset
  equals the injected lag and correlation is high.
- Degenerate case: flat/zero-variance flow → does not crash; returns a finite
  offset.
- Offset precedence: `resolve_offset_args` returns manual override when
  `--sync-offset` set; `(None, True)` when only `--auto-sync`; `(None, False)`
  by default.
- `render_sparkline`: min→`▁`, max→`█`, empty input → safe output, correct width.

## Out of scope

- Multiple videos in one run (concatenating clips that jointly cover the GPX).
- Any change to Stage 2/3 rendering beyond consuming the resolved offset.
