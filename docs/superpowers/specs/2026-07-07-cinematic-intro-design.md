# Cinematic animated intro — Design

**Date:** 2026-07-07
**Status:** Approved for planning (styling + animation approved via rendered demo)
**Context:** The current intro is a black screen with text (MoviePy TextClip). Replace
it with a sporty, animated title card: a dimmed background clip taken from the
curviest part of the ride, a route map that draws itself in, and an overlay with the
route name, date/time, and key stats — matching the HUD's red/white styling.

## Decisions (from brainstorming + approved demo)

- **Animated**, per-frame Pillow overlay (replaces MoviePy TextClip; drops the
  ImageMagick dependency for the intro). Timeline: title/date fade in (0–0.6s), route
  draws in (0.3–3.5s), stat row fades/slides in from ~2.4s and holds to the end.
- **Background = a clip from the ride**, chosen as the **curviest window** of the
  intro's length, dimmed by a ~55% black scrim so attention stays on the overlay.
- Curviness = summed per-second GPS heading change, counted only while moving
  (speed > 8 km/h) to avoid slow GPS jitter winning.
- **The clip selection uses the resolved sync offset** (same `r.offset_used` the rest
  of the pipeline uses), searching only the activity window the video covers, so it
  honours `--sync-offset`/`--auto-sync`.
- Stats: route name (GPX `<name>`, else humanised filename), date + time, distance,
  moving time, avg speed, elevation gain, and power **only if** a watts stream exists.
- Rendered at the configured `cfg.output_height` (the intro overlay scales by
  `k = H/1080`, like the HUD).

## Components

- `intro_select.py` (new, pure):
  - `heading_change_per_sec(coords, speeds_kmh, min_speed_kmh=8.0) -> list[float]` —
    per-second absolute bearing change (great-circle bearing between consecutive
    coords, wrap-normalised to [-180,180], abs), zeroed where speed ≤ min_speed.
  - `curviest_window(turn, offset_seconds, video_duration, clip_len) -> float` —
    returns the **video-time start** of the `clip_len`-second window with the highest
    summed turning, searching activity seconds in
    `[offset, offset + video_duration − clip_len]` and returning `best_activity −
    offset` (clamped ≥ 0). Falls back to 0.0 if the window doesn't fit.
- `intro_renderer.py` (new, Pillow):
  - `render_intro_frame(t_norm, route_pts_px, title, date_str, stats, size, cfg) ->
    Image` — a transparent RGBA frame: ~55% black scrim, red accent bar, title + date
    (fade in), the route polyline drawn to progress `p(t_norm)` with a glow + start
    marker + leading dot, and the stat row (fade/slide in from ~2.4s). Fonts/positions
    scale by `H/1080`. Reuses `minimap.compute_bounds`/`project` to project the full
    route into a centred map box.
  - `intro_stats(gpx, extra_stats) -> list[tuple[str, str]]` — builds the label/value
    pairs (AFSTAND/TIJD/GEM. SNELHEID/HOOGTEMETERS, + VERMOGEN when
    `extra_stats["avg_power"]` present); `intro_title(gpx, gpx_path) -> str` (GPX
    `<name>` or humanised filename); Dutch date via a month-name map.
  - `load_gpx` must expose the GPX `<name>` — add `name: str | None` to `GpxData`
    (parsed from the GPX `<trk><name>` / `<metadata><name>`), default `None`.
- `intro_generator.build_intro_clip` is rewritten to: select the curvy window
  (`curviest_window`), extract that clip from the source video scaled to `W×H`, render
  the intro overlay PNG sequence at `cfg.intro_fps` (new config, default 30), and
  overlay the sequence onto the dimmed clip with FFmpeg. Signature gains what it needs:
  `build_intro_clip(gpx, cfg, out_path, extra_stats, size, video_path, offset_seconds)`.
  If the source has no usable window (video shorter than the intro), fall back to a
  solid dark background (no bg clip) so the intro still renders.
- `intro_generator.build_final_video` passes `args.video`, the resolved
  `offset_seconds`, and `(W, H)` into `build_intro_clip`. It already receives `args`
  and computes `(W, H)`; the resolved offset must be threaded from `main` (see below).
- `config.py`: `intro_fps: int = 30`, `intro_scrim_opacity: float = 0.55`.

## Offset threading

`build_final_video(reel_path, gpx, cfg, output, args, offset_seconds)` gains
`offset_seconds` (default 0.0). `main.py` passes `r.offset_used` in both the segment
and flow render paths (both already have `r`). The intro clip selection and the
existing scale/concat are unchanged otherwise.

## Data flow

```
main: offset = r.offset_used  →  build_final_video(..., offset_seconds=offset)
  → build_intro_clip:
      turn = heading_change_per_sec(gpx.coords, gpx.speeds_kmh)
      clip_start_video = curviest_window(turn, offset, get_video_duration(video), intro_duration)
      ffmpeg: extract [clip_start_video, +intro_duration] from video, scale W×H → bg
      render_intro_frame(...) × (intro_fps*intro_duration) → PNG sequence
      ffmpeg overlay PNG sequence onto bg (shortest) → intro.mp4
  → _concat_intro_and_reel(intro, reel, intro_duration, output, W, H)  (unchanged)
```

## Error handling

- Video shorter than the intro / no fitting window → `curviest_window` returns 0.0 and
  `build_intro_clip` falls back to a solid dark background (renders the overlay on a
  scrim over black), so the intro always succeeds.
- Degenerate route (<2 points) → the map box is skipped (title/date/stats still shown).
- Missing GPX `<name>` → humanise the GPX filename (underscores→spaces, strip ext).
- No watts → power stat omitted (as today).
- The intro overlay encode reuses `_run_ffmpeg_progress` (label "Intro renderen") so
  it shows progress too.

## Testing (pytest)

- `heading_change_per_sec`: a straight line → ~0 turning; a right-angle turn → ~90;
  a turn while stationary (speed ≤ 8) → 0 (jitter suppression).
- `curviest_window`: synthetic `turn` with a clear curvy burst → returns the correct
  video-time start; respects the offset (best activity second minus offset); clamps to
  the `[offset, offset+dur-clip]` range; returns 0.0 when the window doesn't fit.
- `intro_title`: GPX `<name>` used when present; else humanised filename.
- `intro_stats`: includes power only when avg_power present; formats km/moving-time/
  avg-speed/elevation.
- `render_intro_frame`: returns an RGBA image of `size` with non-transparent pixels in
  the title (top-left) and stat-row (bottom) regions, and a full-frame scrim (corner
  pixels have alpha > 0).
- GpxData `<name>` parsing on a synthetic GPX fixture.
- Real-ffmpeg smoke (skipif no ffmpeg): `build_intro_clip` on a tiny synthetic video +
  gpx renders a non-empty clip at the requested size; and the no-bg fallback path
  (video shorter than intro) also renders.

## Out of scope

- Ken-Burns zoom/pan on the background (route-draw + fade is the approved animation).
- Music/audio in the intro (the final concat already supplies a silent track).
- Map tiles / real basemap imagery (the route polyline over the dimmed clip is the look).
