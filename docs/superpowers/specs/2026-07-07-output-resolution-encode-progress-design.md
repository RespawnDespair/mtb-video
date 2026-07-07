# Configurable output resolution + encode progress — Design

**Date:** 2026-07-07
**Status:** Approved for planning
**Context:** The HUD and segment footage are processed at the source resolution (4K),
but `build_final_video` hard-codes `scale=1920:1080` and the MoviePy intro is
1920×1080, so the final output is always downscaled to Full HD. Users want a
configurable target resolution (incl. native/4K), and — because 4K encoding is slow
— a real percentage during the heavy encode steps.

## Decisions (from brainstorming)

- Resolution via `--output-height N` (default 1080) or the literal `source` (match the
  input height). Width follows the source aspect (`scale=-2:H`, even width).
- Encode `%` for the two heavy re-encodes only (per-clip HUD overlay + final
  assembly); the fast `-c copy` concat/music steps keep their status line; the
  per-frame HUD-render `%` is unchanged.

## Part A — configurable output resolution

- `config.py`: `output_height: int = 1080`.
- `main.py`: `--output-height` (string, default `"1080"`). Resolve to an int via a new
  helper `resolve_output_height(value: str, video_path: str) -> int`:
  `"source"` (case-insensitive) → the source video height; otherwise `int(value)`.
  Assign to `cfg.output_height`. Applies to both segment and flow render paths.
- `highlight_detector.py`: `get_video_resolution(path) -> tuple[int, int]` (width,
  height) via the existing `_ffprobe_json` helper.
- **Target dimensions:** `build_final_video` computes the exact output `(W, H)` once
  from the source aspect: `H = cfg.output_height`, `W = _even(round(source_w * H /
  source_h))` (using `get_video_resolution(args.video)`; `_even` rounds down to an
  even number for libx264). Both the intro and the reel are scaled to this exact
  `W×H`, so their dimensions always match for concat regardless of aspect.
- `intro_generator.build_final_video`: replace the two `scale=1920:1080` with
  `scale={W}:{H},setsar=1` (the computed target); `fps=30` unchanged. The reel keeps
  its aspect (it came from the source); the intro is rendered at exactly `W×H` (below),
  so neither is distorted.
- `intro_generator.build_intro_clip`: render at the target `(W, H)` — accept the
  target size, and scale every fontsize/position by `k = H / 1080` so the title card
  stays proportional at any resolution (not tiny at 4K). `build_final_video` passes the
  computed `(W, H)` into it.

## Part B — encode progress

- `video_editor._run_ffmpeg_progress(cmd, total_seconds, label) -> None`:
  - Insert `-progress pipe:1 -nostats` into `cmd` right after `ffmpeg -y`.
  - `subprocess.Popen(..., stdout=PIPE, stderr=PIPE, text=True)`; read stdout line by
    line. ffmpeg `-progress` emits `key=value` lines; parse `out_time_us=` (or
    `out_time_ms=`) → seconds; `pct = min(100, out_seconds / total_seconds * 100)`.
    Print `\r{label} — {pct:>3.0f}%` to stderr (via the existing `_log`).
  - On completion: print a newline; if returncode != 0, raise `RuntimeError` with the
    captured stderr tail (preserving the old `check=True` behaviour).
  - Guard `total_seconds <= 0` (avoid div-by-zero → just show elapsed/■ without %).
- Apply to:
  - The per-clip **HUD overlay** encode in `build_segment_reel` (total = clip
    duration), label `[{i}/{n}] {name} — overlay encoderen`.
  - The **final assembly** encode in `build_final_video` (total = `cfg.intro_duration`
    + reel duration via `highlight_detector.get_video_duration(reel_path)`), label
    `Eindmontage`. `intro_generator` imports `_run_ffmpeg_progress` from
    `video_editor` (no circular import: `video_editor` does not import
    `intro_generator`).
- The lower-third fallback cut, flow-mode `_cut_segment`, and the `-c copy`
  concat/music steps keep their current `_log` status lines (fast / rare).

## Error handling

- `--output-height source` with an unreadable video → ffprobe raises; surface a clear
  error (the render can't proceed without the source anyway).
- Non-16:9 source → target `W` is derived from the source aspect and both intro and
  reel are scaled to the same exact `W×H`, so concat always gets equal dimensions
  (the reel is undistorted; the solid-colour intro is rendered at `W×H` directly).
- `_run_ffmpeg_progress` non-zero exit → RuntimeError with stderr; in
  `build_segment_reel` the existing per-clip `try/except` still falls back to the
  lower-third band.

## Testing (pytest)

- `resolve_output_height`: `"source"` → source height (ffprobe/`get_video_resolution`
  mocked), `"2160"` → 2160, `"1080"`/default → 1080.
- `get_video_resolution`: parses width/height from a mocked `_ffprobe_json`.
- Real-ffmpeg end-to-end (skipif no ffmpeg): render a segment reel + `build_final_video`
  with `output_height=720` on a tiny synthetic clip; ffprobe confirms the output height
  is 720 (exercises the scale + intro-concat path at a non-default resolution).
- Real-ffmpeg smoke: `_run_ffmpeg_progress` runs a short encode → returncode 0, output
  exists (and does not raise).
- Existing suite stays green (HUD scaling unchanged; default 1080 path unchanged).

## Out of scope

- Bitrate/codec presets, HDR, per-resolution quality tuning.
- Progress bars for the fast `-c copy` steps.
