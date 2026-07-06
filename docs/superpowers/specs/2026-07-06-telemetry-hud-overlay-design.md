# Animated telemetry HUD overlay (segment mode) — Design

**Date:** 2026-07-06
**Status:** Approved for planning
**Context:** Follow-up to the Strava-segment highlight mode. The static lower-third
band is replaced (in segment mode) by an animated, per-frame telemetry HUD in the
four corners — segment name + HR, elevation + slope, a speedometer, and a segment
minimap with a live position dot — fed by the GPX per-trackpoint data.

## Data availability (confirmed in the real GPX)

Per trackpoint the GPX has: `lat`/`lon`, `ele` (elevation), `time`, and
`gpxtpx:hr` (heart rate). No per-point power or cadence — so power/cadence are NOT
in the HUD. All HUD values derive from the GPX per-second series (HR direct;
speed, slope, distance computed) except the segment name (from Strava).

## Decisions (from brainstorming)

- Clean own styling, same 4-corner layout as the reference screenshot.
- Full segment minimap with a moving position dot.
- HUD applies in **segment mode only**, replacing the lower-third band. Flow mode
  stays overlay-free.
- **Bottom-left distance = distance within the current segment** (from segment start).
- **Speedometer full-scale = 45 km/h.**

## HUD layout (own styling)

```
┌─────────────────────────────┬───────────────────────────────┐
│ [segment name]              │            ELEVATION   SLOPE   │
│  149 bpm                    │              12 m       -4 %   │
│                             │                                │
│        (original MTB footage, untouched)                     │
│ ┌─────────┐                 │                     ╭───────╮  │
│ │  minimap│                 │                     │  25   │  │
│ │   •pos  │                 │                     │ km/u  │  │
│ └─────────┘ 0.42 km  05-07  │                     ╰───────╯  │
└─────────────────────────────┴───────────────────────────────┘
```

- **Top-left:** segment name (large) + HR below.
- **Top-right:** elevation (m) + slope (%) accent panels.
- **Bottom-right:** speedometer (arc + number, km/h, full-scale 45).
- **Bottom-left:** segment minimap (polyline from the segment's GPX lat/lon) with a
  moving position dot, + distance-within-segment (km) + ride date including the year
  (format `DD-MM-YYYY`).

## Data flow

```
GPX → extended per-second series: speeds_kmh, elevations_m, hr_bpm, cum_distance_m, coords
segment effort (name, activity time window) + resolved sync offset
For each video frame t in the clip:
    activity_t = t_video + offset  →  sample_telemetry(gpx, activity_t)
       → TelemetrySample(speed_kmh, elevation_m, slope_pct, hr_bpm, lat, lon, seg_distance_km)
    hud_renderer.render_hud_frame(sample, segment, minimap_base, size, cfg) → RGBA overlay
PNG sequence at cfg.hud_fps → FFmpeg overlay onto the clip → re-encode → concat → music → intro
```

`seg_distance_km` = `cum_distance_m(activity_t) − cum_distance_m(segment_start_activity_s)`,
in km, clamped ≥ 0.

## Components

- `highlight_detector.py` — extend `load_gpx`/`GpxData` with per-second, forward-filled
  `elevations_m: list[float]`, `hr_bpm: list[float | None]`, `cum_distance_m: list[float]`
  (parse the `gpxtpx:hr` extension). New fields get defaults so existing construction
  sites are unaffected.
- `telemetry.py` (new):
  - `@dataclass TelemetrySample`: `speed_kmh`, `elevation_m`, `slope_pct`, `hr_bpm`
    (`float | None`), `lat`, `lon`, `seg_distance_km`.
  - `sample_telemetry(gpx, activity_time_s, segment_start_s) -> TelemetrySample`:
    linear interpolation between the bracketing per-second samples; clamps to series
    bounds.
  - `slope_pct(gpx, activity_time_s, window_s=3) -> float`: `Δelevation / Δhorizontal_distance × 100`
    over ±`window_s`, 0 when horizontal distance ≈ 0; clamped to a sane range (±40%).
- `minimap.py` (new, pure):
  - `project_track(coords, box_w, box_h, pad) -> list[tuple[float, float]]`:
    equirectangular projection (scale lon by cos(mean lat)), fit-to-box preserving
    aspect ratio, padded, y flipped for image space.
  - `project_point(lat, lon, coords, box_w, box_h, pad) -> tuple[float, float]`:
    same transform for the live position.
- `hud_renderer.py` (new, Pillow):
  - `render_speedometer(draw, box, speed_kmh, max_kmh, cfg)`: arc gauge + number.
  - `render_stat_panel(img, box, label, value, cfg)`: red accent label + value (used
    for elevation and slope).
  - `render_name_hr(img, box, name, hr_bpm, cfg)`: segment name + HR.
  - `render_minimap_base(track_pts, box, cfg) -> Image`: draws the segment polyline
    once per segment (cached), returned as an RGBA tile.
  - `render_hud_frame(sample, segment, minimap_base, track_pts, size, cfg) -> Image`:
    composites all widgets + the live position dot onto a transparent RGBA frame at
    the video resolution.
- `video_editor.py` — `build_segment_reel` gains a HUD path: for each clip, sample
  telemetry per frame (at `cfg.hud_fps`), render HUD PNGs to a temp dir, and overlay
  with FFmpeg (`-framerate hud_fps -i hud_%06d.png` over the clip) instead of
  `_lower_third_filter`. When `cfg.hud_enabled` is False (or Pillow render raises),
  fall back to the existing `_lower_third_filter` band.
- `config.py` — `hud_enabled: bool = True`, `hud_fps: int = 15`,
  `speedo_max_kmh: float = 45.0`, plus HUD color/font-size fields (reuse
  `overlay_font_path`).

## Rendering & performance

- Only the segment windows are decoded (not the whole 4.6 GB file).
- HUD rendered at `hud_fps=15`; FFmpeg holds each overlay frame across the 30 fps
  video, halving the frames drawn. The minimap base is rendered once per segment;
  per frame only the moving dot is added.
- Frame time → activity time uses the resolved sync offset (same as the rest of the
  pipeline). Frames are indexed from the clip start.

## Error handling

- Missing HR at a sample → HR omitted from that frame (no "bpm" line).
- Degenerate minimap (all points identical / <2 points) → draw just the dot / skip
  the polyline; never divide by zero.
- Pillow/render failure → log a warning and fall back to the lower-third band so a
  render still completes.
- Slope with ~zero horizontal movement → 0%.

## Testing (pytest)

- GPX loader: `gpxtpx:hr` parsed; per-second `elevations_m`/`hr_bpm`/`cum_distance_m`
  correct on a synthetic fixture (incl. a gap → forward-fill).
- `sample_telemetry`: interpolation between seconds; `seg_distance_km` measured from
  the segment start; `slope_pct` on a known grade.
- `minimap.project_track`/`project_point`: known lat/lon → expected in-box pixels;
  aspect ratio preserved; degenerate (single point) safe.
- `hud_renderer.render_hud_frame`: output has the video dimensions and non-transparent
  pixels in the expected corner regions (name top-left, speedo bottom-right, stats
  top-right, minimap bottom-left).
- Real-ffmpeg overlay smoke (skipif no ffmpeg): a HUD PNG sequence overlaid on a
  synthetic clip renders (returncode 0, non-empty). A sample frame is rendered for
  user approval before the full build.

## Out of scope

- Realtime power/cadence (not in the GPX).
- HUD in flow mode (segment-mode only).
- Matching the reference screenshot's exact template styling (we use our own).
