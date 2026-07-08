# Input/output folder structure + generated output names — Design

**Date:** 2026-07-08
**Status:** Approved for planning
**Context:** Tidy up how footage goes in and renders come out. Today `--video` is a
required explicit file list and `--output` defaults to `highlight.mp4` in the CWD. We
want: `--video` to also accept a folder (and default to a checked-in `video_input/`);
renders to land in a `video_output/` folder (overridable); and `--output` to become an
optional name override — when omitted, generate the name from the Strava/GPX activity
name + date.

## Decisions (from brainstorming)

- **Input:** `--video` accepts a directory (all `.mp4`/`.mov` in it, non-recursive,
  case-insensitive, sorted by filename) or a list of files, or a mix. It becomes
  **optional**; when omitted, the tool uses `video_input/`. No usable videos → clear error.
- **Output folder:** `--output-dir` (default `video_output/`); created if absent.
- **Output name:** `--output` optional. Omitted → generated
  `sanitize(gpx.name)_YYYY-MM-DD.mp4` (e.g. `Stellendam_Goedereede_2026-07-05.mp4`); no
  name → `highlight_YYYY-MM-DD.mp4`. A bare filename → placed in the output folder; a
  value containing a path separator → used verbatim (full control, backward compatible).
- **Checked-in folders:** `video_input/README.md` and `video_output/README.md` explain
  their purpose and keep the (otherwise empty) folders in the repo; the media stays
  git-ignored (`*.mp4`/`*.mov` already ignored — no `.gitignore` change needed).

## Components (all in `main.py` unless noted)

- `gather_videos_in_dir(directory) -> list[str]`: sorted `.mp4`/`.mov` files (case-
  insensitive) directly in `directory` (non-recursive). Empty list if none.
- `resolve_video_inputs(args) -> list[str]`: `paths = args.video or [DEFAULT_INPUT_DIR]`
  (`DEFAULT_INPUT_DIR = "video_input"`). For each path: a directory → extend with
  `gather_videos_in_dir`; a file → append; otherwise `SystemExit` (path not found). If
  the result is empty → `SystemExit` ("no videos in <video_input/ or given paths>").
  Returns the ordered list.
- `_sanitize_filename(name) -> str`: collapse whitespace to `_`, keep `[A-Za-z0-9_-]`,
  drop other characters, collapse repeated `_`, strip leading/trailing `_`, truncate to a
  sane length (~100 chars). Returns `""` if nothing usable remains.
- `generate_output_name(gpx) -> str`: `base = _sanitize_filename(gpx.name or "") or
  "highlight"`; `date = gpx.start_time.strftime("%Y-%m-%d")`; return `f"{base}_{date}.mp4"`.
- `resolve_output_path(args, gpx) -> str`:
  - if `args.output`: if it contains a directory separator → return it verbatim; else →
    `os.path.join(args.output_dir, args.output)`.
  - else → `os.path.join(args.output_dir, generate_output_name(gpx))`.
  - `os.makedirs(os.path.dirname(resolved), exist_ok=True)` before returning (create the
    target folder). Return the path.
- CLI (`build_parser`):
  - `--video`: `nargs="*", default=None` (was `required=True, nargs="+"`); help updated
    to "video files or a folder; defaults to video_input/".
  - `--output`: `default=None` (was `"highlight.mp4"`); help "output filename (in
    --output-dir) or a full path; default: generated from the activity name + date".
  - `--output-dir`: new, `default="video_output"`, help "folder for rendered output".
- `main()` wiring:
  - Early (before `resolve_output_height`): `args.video = resolve_video_inputs(args)` —
    normalises `args.video` to the resolved non-empty list, so every existing
    `args.video[0]` / `len(args.video)` / `build_clip_sources(args.video, …)` /
    `build_final_video` (single-path `args.video[0]`) keeps working unchanged.
  - After `gpx = resolve_gpx_source(args)`: `output_path = resolve_output_path(args, gpx)`.
  - Pass `output_path` (instead of `args.output`) into both `build_final_video(...)` calls
    and the final `print(f"Wrote {output_path}")` in the single path. For the multi path,
    add an explicit `output_path` parameter to `_run_multi(args, cfg, gpx,
    offset_override, use_auto, output_path)` and use it there (build_final_video +
    "Wrote"); `main()` passes the resolved `output_path` when it calls `_run_multi`.
- New files: `video_input/README.md`, `video_output/README.md` (placeholders).

## Data flow

```
--video (files | folder | omitted)        --output / --output-dir
     → resolve_video_inputs → args.video = [ordered files]
gpx = resolve_gpx_source(args)
     → output_path = resolve_output_path(args, gpx)   (folder created)
existing pipeline (single or multi) → build_final_video(..., output_path)
```

## Error handling

- `--video` omitted and `video_input/` empty/absent → `SystemExit` naming the folder.
- A `--video` path that is neither file nor directory → `SystemExit` naming it.
- A `--video` folder with no `.mp4`/`.mov` → contributes nothing; if the overall result
  is empty, the "no videos" `SystemExit` fires.
- Output folder auto-created; a verbatim `--output` path's parent dir auto-created too.

## Testing (pytest, pure — no ffmpeg)

- `gather_videos_in_dir`: temp dir with `b.mp4`, `a.mov`, `c.txt`, `d.MP4` → returns the
  three videos sorted, excludes `.txt`, case-insensitive.
- `resolve_video_inputs`: explicit file list → unchanged; a folder arg → expanded sorted;
  omitted (`args.video=None`) with a monkeypatched/temp `video_input` → its videos; empty
  → `SystemExit`; a non-existent path → `SystemExit`.
- `_sanitize_filename`: `"MTB Goeree Vol Gas!"` → `"MTB_Goeree_Vol_Gas"`; slashes/colons
  dropped; empty/garbage → `""`.
- `generate_output_name`: gpx with name + `start_time` → `"<sanitised>_2026-07-05.mp4"`;
  `gpx.name = None` → `"highlight_2026-07-05.mp4"`.
- `resolve_output_path`: no `--output` → `<output_dir>/<generated>` and the dir exists;
  bare `--output ride.mp4` → `<output_dir>/ride.mp4`; `--output sub/x.mp4` (has separator)
  → verbatim `sub/x.mp4`; `--output-dir` override respected. Use `tmp_path` for dirs.
- CLI parsing: `--video` optional (absent → `None`); `--output-dir` default
  `"video_output"`; `--output` default `None`.

## Out of scope

- Recursive input scanning; extensions beyond `.mp4`/`.mov`.
- Moving music input into this folder scheme (music already takes a folder via `--music`).
- Auto-cleaning or rotating `video_output/`.
