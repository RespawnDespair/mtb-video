# Multiple video files for one ride — Design

**Date:** 2026-07-07
**Status:** Approved for planning
**Context:** Feature #3 of three (auto-GPX ✓ → auto-music ✓ → **multi-clip**). Today
`--video` takes one file and the whole pipeline assumes a single continuous recording:
`activity_t = video_t + offset`, one scalar offset for the ride. The user records one
ride as several separate clips from the same camera. The segment/highlight **selection
stays exactly as it is** (it picks the ride-time ranges we want); what's new is
resolving *which source file(s) cover each selected range* and cutting from the right
file — rendering only the ride portions that actually have footage.

## Decisions (from brainstorming)

- The clips are loose recordings from **one ride, one GPX** (not contiguous, not
  multiple rides). Selection runs on the ride timeline unchanged.
- Each file is placed on the ride timeline by its **own recording time**
  (`creation_time` metadata → filename `VID_YYYYMMDD_HHMMSS` → file mtime), i.e. the
  existing offset chain applied per file. The **relative spacing** between files (same
  camera) is trusted. `--sync-offset` **anchors the earliest-recorded file** at that
  value — the same absolute meaning it has for a single file — and the others follow by
  their recording-time deltas, so one value corrects a constant camera-clock skew.
  (Correction superseded: an earlier draft *added* `--sync-offset` to every file's own
  offset, which double-counted the anchor file's own offset and mis-aligned everything;
  anchoring the earliest file matches single-file semantics.)
  - Worked example (`--sync-offset 577`, files A@own-offset 300 s and B@own-offset
    1080 s): A is earliest → anchored at **577 s**; B = 577 + (1080 − 300) = **1357 s**.
    Same delta (780 s) between them, anchored so A matches its single-file placement.
- **Offset semantics fork (backward compatible):** with **one** `--video` file,
  `--sync-offset` keeps its current meaning (absolute override of that file's offset).
  With **multiple** files it is the shared additive correction on each file's own
  recording time. One file with no `--sync-offset` → byte-identical to today.
- For each selected ride-time range: fully covered by one file → cut it; partially
  covered → cut only the covered part; spanning a file boundary → cut each covered
  piece from its file (kept in order); no coverage → drop the range with a warning.
- Final parts are concatenated in **ride-chronological order**. HUD/minimap per part
  uses that part's source-file offset. Intro background: the curviest window is searched
  across **all** files' covered spans and cut from the winning file. Music (feature #2)
  is unchanged — it spans the whole final video.
- Different resolutions between files → each scaled to the target output height
  (existing scale logic) + a warning. Two files covering the same ride moment (overlap)
  → keep one (earliest-starting, then longest), with a warning.

## Core representation

Everything downstream of selection is expressed as **render parts** in each source
file's local time, tagged with the ride-time offset for telemetry:

- `ClipSource` (new): `path: str`, `base_offset: float` (activity-seconds at the file's
  local t=0), `duration: float`, `width: int`, `height: int`,
  `creation_time: datetime`, `offset_source: str`. Coverage on the ride =
  `[base_offset, base_offset + duration]`.
- `RenderPart` (new): `source_path: str`, `local_start: float`, `local_end: float`,
  `base_offset: float` (for HUD: `activity_t = local_t + base_offset`),
  `name: str | None`, `stats: dict | None` (carried through from Strava segments).

## Components

- `clip_sources.py` (new):
  - `build_clip_sources(video_paths, gpx, cfg, offset_override=None, use_auto=False)
    -> list[ClipSource]`: for each path, resolve the base offset. Single path: reuse
    `resolve_sync_offset` (manual override stays absolute). Multiple paths: resolve each
    file's base from `resolve_sync_offset` with `offset_override=None` (so it uses
    metadata/filename/mtime), then add the shared correction `offset_override or 0.0`.
    Probe `get_video_duration` / `get_video_resolution` per file. Sort by `base_offset`;
    warn on resolution mismatch and on coverage overlap between files.
  - `resolve_render_parts(sources, ranges) -> list[RenderPart]`: `ranges` is a list of
    `(activity_start, activity_end, name, stats)` in ride-seconds. For each range,
    intersect with every source's coverage; emit a `RenderPart` per non-empty
    intersection (`local_start = activity_start_clipped - source.base_offset`), split
    across files where needed, drop ranges with no coverage (collect their names for a
    warning), and on multi-file overlap keep the earliest/longest source only. Return
    parts sorted by ride time (`base_offset + local_start`).
- `segment_detector.py`: add `efforts_to_activity_ranges(gpx, efforts) -> list[tuple]`
  producing `(activity_start, activity_end, name, stats)` directly from effort
  `start_date`/`elapsed_time` (NO offset subtraction, NO single-file duration clamp —
  clamping to footage now happens in `resolve_render_parts`). The existing
  `efforts_to_clips` stays for the single-file internal path if needed, but the
  multi-file path uses the new function.
- `highlight_detector.py` (flow mode): analysis stays per file. `analyze_video` runs on
  each source; its video-local `Segment(start,end)` ranges convert to activity ranges
  via that source's `base_offset` (`activity = local + base_offset`). Because each
  highlight lives within one file, its source is known directly — these become
  `RenderPart`s without needing coverage search.
- `video_editor.py`: replace the single-`video_path` assumption with a parts-driven
  builder `build_reel_from_parts(parts, cfg, gpx=None, telemetry_source=None) -> str`:
  cut each `RenderPart` from `part.source_path` (`-ss local_start -t (local_end-
  local_start)`), render HUD with `activity_t = local_t + part.base_offset`, scale to the
  target height, concat in order (the concat demuxer already accepts parts from
  different source files). `build_highlight_reel` / `build_segment_reel` become thin
  adapters that build parts and call this (or are replaced by it). `_cut_segment` and
  `_render_hud_pngs` take a per-part source path + base offset instead of one shared
  `video_path`/`offset_seconds`.
- `intro_generator.py` / `intro_select.py`: `curviest_window` searches across all
  `ClipSource` covered spans and returns `(source_path, local_start)`; the intro
  background is cut from the winning file. `build_final_video` no longer reads a single
  `args.video` for resolution — it takes the target `(W, H)` from the reconciled sources
  (all scaled to `cfg.output_height`).
- `main.py`: `--video` becomes `nargs="+"` (`args.video` is a list). Build the
  `ClipSource` list once; feed selection output through `efforts_to_activity_ranges`
  (segment mode) or per-file flow analysis (flow mode) → `resolve_render_parts` →
  `build_reel_from_parts` → `build_final_video`. `resolve_output_height` and the sync
  report use the source list. A single-element `--video` reproduces today's behavior.

## Data flow

```
--video A.mp4 B.mp4 C.mp4  +  gpx
  → build_clip_sources → [ClipSource(base_i, dur_i, …)] sorted by ride time
  → selection (unchanged): Strava efforts | flow highlights → activity-time ranges
  → resolve_render_parts(sources, ranges) → [RenderPart] (split/clamped/dropped)
  → build_reel_from_parts (cut per source, HUD per base_offset, concat in ride order)
  → build_final_video (intro across sources, music over whole video)
```

## Error handling

- One `--video` file → existing single-file path, unchanged (regression-tested).
- A selected range with no footage → dropped; names collected and warned once
  (`[warn] geen video voor: <namen>`).
- No selected range has any coverage → clear error (nothing to render), exit non-zero.
- Files with different resolution → scaled to target height + warning; different
  orientation (portrait vs landscape) → scaled into the target frame (letterbox via the
  existing scale path), warned.
- Overlapping coverage between two files → keep earliest/longest, warn.
- A source with no resolvable recording time (no metadata/filename/mtime) → error naming
  the file (can't place it on the ride).

## Testing (pytest)

- `build_clip_sources`: mocked probes → per-file base offsets from
  metadata/filename/mtime; shared `--sync-offset` correction added to every file;
  single-file manual override stays absolute; sorted by ride time; resolution-mismatch
  and overlap warnings fire.
- `resolve_render_parts` (pure, no ffmpeg — the core logic, tested hardest):
  - range fully inside one source → one part with correct local times;
  - range partially covered → clamped to coverage;
  - range spanning two adjacent sources → two parts, correct split, ride order;
  - range with no coverage → dropped and reported;
  - overlapping sources → one part from the kept source;
  - `base_offset` carried onto each part for HUD.
- `efforts_to_activity_ranges`: effort start/elapsed → activity ranges with name/stats,
  no offset applied.
- `build_reel_from_parts` (real-ffmpeg, skipif): parts from two different generated
  source files concatenate into one reel of the summed duration with an audio+video
  stream; HUD frames sample telemetry at `local + base_offset`.
- `curviest_window` across sources: returns the window+source with the highest curviness
  regardless of which file it's in.
- End-to-end regression: single `--video` file produces the same result as before
  (offset, selection, reel) — no behavioral change for the existing use.

## Out of scope

- Multiple rides / multiple GPX tracks (a larger, separate feature).
- Filling ride-time gaps between clips with anything (uncovered = dropped).
- Re-timing/altering telemetry; de-duplicating overlapping footage beyond "keep one".
- Per-file independent motion auto-sync (the shared `--sync-offset` correction + per-file
  recording time is the model; `--auto-sync` still estimates a single correction).
