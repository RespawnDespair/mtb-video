# Local web GUI (v1) — Design

**Date:** 2026-07-08
**Status:** Approved for planning (mockup approved: artifact 4cd772a4)
**Context:** The CLI does everything, but a GUI makes it far easier to browse Strava
activities, **align the video clips against the ride's telemetry** (the key feature),
pick music/output, and run a render. Build a local web app: a small FastAPI backend that
reuses the existing modules as a library, serving the approved mockup UI in the browser.
`python gui.py` starts it and opens the browser. Cross-OS, low-threshold, primarily Mac.

## Decisions (from brainstorming)

- **Stack:** FastAPI + uvicorn backend, static HTML/CSS/JS frontend (the approved mock),
  browser-based. Launched with `python gui.py`. Bind to `127.0.0.1` only.
- **Reuse, don't duplicate:** the backend calls existing functions (`strava_client`,
  `strava_gpx`, `highlight_detector`, `clip_sources`, `segment_detector`,
  `main.gather_videos_in_dir` / `generate_output_name`, etc.).
- **Rendering runs the CLI:** `POST /api/render` assembles the exact `main.py` argv from
  the config and runs it as a **subprocess**, streaming stdout/stderr to the UI. The UI
  shows the same command it runs. One render at a time; a Stop kills the subprocess.
- **Folder/file selection:** native OS dialog (`osascript` on macOS, `tkinter.filedialog`
  fallback) via `POST /api/pick`, returning absolute path(s).
- **Alignment:** a telemetry timeline (downsampled ride speed profile + Strava segment
  bands) with the video clips positioned by their recording-time offset + a shared
  `--sync-offset`; dragging the offset repositions clips live. Per rendered segment a
  **start-frame preview** (`GET /api/frame`) that updates (throttled ~150 ms) as the
  offset changes; "geen beeld" when no clip covers that segment.
- **Track source:** a Strava activity (auto-GPX) OR a chosen `--gpx` file (CLI supports
  both).

## Architecture / files

- `gui.py` (repo root) — launcher: `uvicorn.run(gui.server:app, host=127.0.0.1, port=8000)`
  and `webbrowser.open` (respect `--no-browser`/`--port`).
- `gui/server.py` — the FastAPI `app`: API routes + static mount (`/` → `index.html`).
- `gui/static/index.html`, `gui/static/app.css`, `gui/static/app.js` — the frontend from
  the approved mock, split into files and wired to the API.
- `strava_client.list_activities(n=15) -> list[dict]` (new) — recent activities
  (id, name, start_date, distance, moving_time, total_elevation_gain, type) from
  `/athlete/activities` (the pattern already used inside `get_activity_stats`).
- `gui/render.py` — `build_render_argv(cfg: dict) -> list[str]` (pure, unit-tested):
  turns the GUI config into the `main.py` argv (only sets flags that are present); plus
  the subprocess runner/streamer.
- `requirements.txt`: add `fastapi`, `uvicorn`.

## Backend endpoints

- `GET /api/activities` → `list_activities`; on not-configured → `503` with a message the
  UI shows (with the `strava_auth.py` hint and the GPX-file fallback).
- `POST /api/pick` `{mode: "folder"|"files"|"gpx"|"save"}` → native dialog; returns
  `{paths: [...]}` / `{path: "..."}` or `{cancelled: true}`. Runs the dialog off the event
  loop (thread); osascript on darwin, tkinter elsewhere.
- `POST /api/videos` `{paths|dir}` → `[{path, name, duration, width, height}]` (reuse
  `gather_videos_in_dir` + a cached ffprobe helper).
- `GET /api/music?dir=` → immediate subfolders with audio-track counts, and the track
  count of `dir` itself (so the UI can list `music/rock`, `music/bass`, …).
- `GET /api/timeline` `?activity_id=|gpx=&offset=&videos=` → the alignment payload:
  `{duration, speed: [downsampled ~300 pts], segments: [{n, name, start, end, noteworthy}],
  clips: [{file, base, start, end}]}`. Builds `GpxData` (via `gpx_from_strava` or
  `load_gpx`, cached per source), derives `clips` from `clip_sources.build_clip_sources`
  with the given offset, and `segments` from `get_segment_efforts` →
  `efforts_to_activity_ranges`.
- `GET /api/frame?video=&t=&w=320` → one JPEG via `ffmpeg -ss {t} -i {video}
  -frames:v 1 -vf scale={w}:-2 -q:v 4 -f mjpeg pipe:1`, `Content-Type: image/jpeg`,
  cached per `(video, round(t), w)`.
- `POST /api/auto-align` `{video, activity_id|gpx}` → runs the existing motion estimator
  (`compute_optical_flow_per_second` + `estimate_offset_by_motion`) on the primary clip →
  `{offset, correlation}`. (May take a while — the UI shows a spinner.)
- `POST /api/render` `{config}` → `build_render_argv` → subprocess
  `[sys.executable, "main.py", ...]`; **StreamingResponse** of the command (first line)
  then stdout/stderr lines as they arrive. `POST /api/render/stop` terminates it.

## Data flow

```
browser (index.html/app.js)
  GET /api/activities ─────────────→ strava_client.list_activities
  POST /api/pick (folder/files) ───→ native dialog → paths
  POST /api/videos ────────────────→ gather_videos_in_dir + ffprobe
  GET /api/timeline (offset) ──────→ GpxData + build_clip_sources + segment efforts
  GET /api/frame (per segment) ────→ ffmpeg fast-seek JPEG        (throttled on drag)
  POST /api/render (config) ───────→ build_render_argv → subprocess main.py → stream
```

## Error handling

- Strava not configured → `/api/activities` 503 with a clear message; the GPX-file path
  stays available so the GUI still works offline.
- `/api/frame` for an uncovered time / missing file → 404; the UI shows "geen beeld".
- `/api/timeline` with no segments (flow mode / no Strava) → returns `segments: []`; the
  timeline still shows the speed profile and clip bars.
- Render subprocess non-zero exit → the stream ends with the exit code; the UI marks it
  failed and shows stderr. Stop → SIGTERM (then SIGKILL after a grace period).
- Only one render at a time; a second `POST /api/render` while running → 409.
- Bind `127.0.0.1` only (the API runs the CLI with user paths — not for exposure).

## Testing (pytest)

- `build_render_argv` (pure): a full config → the exact expected argv; optional flags
  (no music, no `--pick`, gpx-vs-strava, custom output/dir) included/omitted correctly.
- `strava_client.list_activities` (mocked requests): returns normalised dicts; raises
  when unconfigured.
- FastAPI `TestClient`: `/api/activities` (mock `list_activities`); `/api/videos` on a
  tmp dir of generated clips → durations/resolutions; `/api/timeline` with a mocked
  `GpxData` + mocked efforts → shape (duration, speed length, segments, clips with base
  offsets); `/api/frame` (real-ffmpeg, skipif) on a generated clip → JPEG bytes; `/` →
  serves the HTML.
- `/api/render` (real, light): a config pointing at a tiny generated clip + mocked track
  streams to completion with an exit code; `/api/render/stop` terminates a run.
  `/api/pick` native dialog is not unit-tested (OS-interactive) — kept thin, exercised
  manually.

## Out of scope (v1)

- Saved presets / recent-config history; multi-render queue.
- Video scrubbing / dragging each clip independently (offset is shared).
- Packaging as a native `.app`; a Strava-auth wizard inside the GUI (`strava_auth.py`
  stays the setup step).
- Auth/multi-user — it's a single-user local tool.
