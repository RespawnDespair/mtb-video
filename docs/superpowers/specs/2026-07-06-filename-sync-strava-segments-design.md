# Filename sync source + Strava-segment highlights — Design

**Date:** 2026-07-06
**Status:** Approved for planning
**Context:** Follow-up to ride-highlight-editor + the sync-debug feature. Two
insights from real footage: (A) the MP4 filename (`VID_YYYYMMDD_HHMMSS_..`)
carries an indicative start timestamp usable for sync; (B) for MTB, interesting
terrain is often technical-but-slow, so speed alone misses it — Strava *segments*
are a better highlight signal. Each Strava segment effort should become a
highlight clip with a lower-third summary (name, time, speed, power, HR).

## Goals

- A: Use the filename timestamp as an additional sync-offset source and surface
  all sources in `--inspect`.
- B: Add a Strava-segment-driven highlight mode: one clip per qualifying segment
  effort that overlaps the video, each with a lower-third stats overlay.

## Decisions (from brainstorming)

- A and B ship in one spec/plan.
- Segment mode is the new default via `--mode auto`; the existing speed/optical-flow
  detection is retained as the fallback (used when Strava/segments are unavailable).
- The flow-based `--auto-sync` is retained.
- Segment filter: keep an effort if it has any Strava achievement (medal/KOM/top-N)
  OR a PR (`pr_rank` set) OR the segment is starred.

## Part A — filename timestamp sync source

- `parse_filename_datetime(name: str) -> datetime | None` — regex
  `VID_(\d{8})_(\d{6})` → a **naive** local `datetime` (pure/testable). Non-matching
  names return `None`.
- `get_filename_timestamp(path: str) -> datetime | None` — applies
  `parse_filename_datetime` to the basename, localizes the naive time using the
  system local timezone, and returns it as tz-aware UTC (or `None`).
- **Offset precedence (updated):** `--sync-offset` (manual) > `--auto-sync` (auto) >
  `creation_time` metadata > **filename** > file mtime. So filename is used only
  when the `creation_time` tag is absent; it beats the mtime fallback.
- `Analysis` gains `filename_offset: float | None`. `offset_source` gains the value
  `"filename"`.
- `--inspect` Sync section lists every available source (metadata, filename, auto,
  mtime) and which one is selected.

## Part B — Strava-segment highlights

### Data acquisition (strava_client.py)

- `get_segment_efforts(activity_id: str) -> list[dict]` — calls
  `GET /activities/{id}?include_all_efforts=true` and returns the
  `segment_efforts` list. Requires Strava configured (raises `RuntimeError`
  otherwise, caller catches). Each effort exposes: segment `name`,
  `start_date` (UTC), `elapsed_time` (s), `moving_time`, `distance` (m),
  `average_watts`, `average_heartrate`, `max_heartrate`, `pr_rank`,
  `achievements` (list), and `segment.starred`.

### Segment → clip mapping (segment_detector.py, new)

- `@dataclass SegmentEffort` — normalized fields parsed from the raw dict:
  `name: str`, `start_date: datetime` (UTC), `elapsed_time: float`,
  `distance_m: float`, `average_watts: float | None`,
  `average_heartrate: float | None`, `pr_rank: int | None`,
  `has_achievement: bool`, `starred: bool`.
- `parse_efforts(raw: list[dict]) -> list[SegmentEffort]`.
- `is_noteworthy(effort, cfg) -> bool` — `effort.has_achievement or
  effort.pr_rank is not None or effort.starred`.
- `@dataclass SegmentClip` — `start: float`, `end: float` (video seconds),
  `name: str`, `stats: dict` (speed_kmh, power_w, hr_bpm, elapsed_s).
- `efforts_to_clips(efforts, gpx_start, offset_seconds, video_duration, cfg) ->
  list[SegmentClip]`:
  - For each effort passing `is_noteworthy`:
    `activity_start_s = (effort.start_date − gpx_start).total_seconds()`;
    `video_start = activity_start_s − offset_seconds`;
    `clip = [max(0, video_start), min(video_duration, video_start + elapsed_time)]`.
  - Keep clips with length ≥ `cfg.min_segment_seconds` that intersect
    `[0, video_duration]`. Sort chronologically by `start`.
  - `stats`: `speed_kmh = distance_m / elapsed_time * 3.6` (0 if elapsed 0),
    `power_w = round(average_watts)` if present, `hr_bpm = round(average_heartrate)`
    if present, `elapsed_s = elapsed_time`.
- `format_segment_stats(clip) -> str` — e.g. `"4:10 · 16.3 km/u · 175 W · 153 bpm"`,
  omitting missing fields (power/HR may be absent).

### Rendering (video_editor.py)

- `_lower_third_filter(name: str, stats: str, cfg) -> str` — builds an FFmpeg
  filtergraph fragment: a semi-transparent `drawbox` band across the lower portion
  plus two `drawtext` lines (name bold-ish larger, stats smaller). Escapes
  `:`, `'`, `\`, `%` in text. Uses `cfg.overlay_font_path` (macOS default with a
  fallback) and `cfg.overlay_*` sizing/opacity settings.
- `build_segment_reel(video_path, clips: list[SegmentClip], music_path, cfg) -> str`
  — cuts each clip (re-encode, since overlay requires it), applies its lower-third,
  concatenates, and mixes music. The concat + music-mix logic is extracted from the
  existing `build_highlight_reel` into a shared helper (`_concat_and_music`) so both
  reel builders reuse it (DRY).
- `build_highlight_reel` (existing flow mode) is refactored to call the shared
  helper; its externally observable behavior is unchanged.

### Mode selection (main.py)

- New flag `--mode {auto,segments,flow}` (default `auto`).
- `auto`: if Strava is configured, `--strava-activity-id` is provided, and
  `efforts_to_clips` yields ≥1 clip in the video window → segment mode; otherwise
  flow mode (current behavior).
- `segments`: force segment mode; exit with a clear error if Strava unconfigured,
  no activity id, or no qualifying clips.
- `flow`: force the existing speed/optical-flow detection.
- The chosen sync offset (Part A precedence, incl. `--sync-offset`/`--auto-sync`)
  feeds `efforts_to_clips`, so segment timing honors the corrected offset.
- **Efficiency:** the optical-flow pass is expensive on large clips. Segment mode
  resolves the offset WITHOUT computing optical flow unless `--auto-sync` is set
  (flow is only needed for auto-sync and for flow-mode detection). Factor offset
  resolution so it can run flow-free: a helper `resolve_sync_offset(video_path, gpx,
  cfg, offset_override, use_auto)` that computes metadata/filename/mtime offsets
  always and the flow-based auto offset only when `use_auto` is True, returning the
  resolved `(offset_used, offset_source, ...)`. `analyze_video` (flow mode) reuses it.
- `--dry-run` in segment mode prints the selected segment clips (name, video window,
  stats) and renders nothing. `--inspect` is unchanged except for the filename source.

### config.py additions

- `overlay_font_path: str` — default a common macOS TTF (e.g.
  `/System/Library/Fonts/Supplemental/Arial.ttf`) with a documented fallback if
  absent.
- `overlay_band_opacity: float = 0.5`, `overlay_name_fontsize: int = 48`,
  `overlay_stats_fontsize: int = 32`.

## Error handling

- Strava failure / not configured in `auto` mode → warn and fall back to flow mode.
- `segments` mode with no data → clear error, exit 1.
- Missing font file → fall back to a bundled/known alternative; if none, error with a
  clear message naming `config.overlay_font_path`.
- Effort with `elapsed_time == 0` → speed 0, still rendered if it passes filters.
- Filename parse failure → `filename_offset = None`; precedence skips it.

## Testing (pytest, test_sync.py)

- `parse_filename_datetime`: `VID_20260705_144402_00_003.mp4` → naive datetime
  (2026-07-05 14:44:02); non-matching name → `None`.
- Precedence: filename beats mtime, loses to metadata/auto/manual (extend
  `_resolve_offset` tests with a filename source).
- `efforts_to_clips`: synthetic efforts + known offset → correct video-time windows;
  filters out-of-window efforts and non-noteworthy efforts; sorts chronologically;
  computes stats (speed from distance/elapsed).
- `is_noteworthy`: achievement OR pr_rank OR starred → True; none → False.
- `format_segment_stats`: full line; omits missing power/HR.
- `_lower_third_filter`: contains a drawbox and the (escaped) name + stats; a name
  with `:`/`'` is escaped.
- A real-ffmpeg overlay smoke test (skipif no ffmpeg): generate a synthetic clip,
  apply `_lower_third_filter`, assert the output renders with 1 video stream — proving
  the drawtext/drawbox filter is valid on this machine.

## Out of scope

- Multiple videos in one run (still a separate future follow-up).
- Fetching segment leaderboards / others' times; only the athlete's own effort data.
