# ride-highlight-editor — Design

**Date:** 2026-07-06
**Status:** Approved for planning

## Purpose

A local (macOS) Python tool that automatically generates highlight videos from
mountainbike action-cam footage. It is **camera-agnostic**: GPS/telemetry comes
from Strava or Garmin Connect GPX exports (not the video file), and is
synchronized to the video timeline using the video file's creation timestamp and
the activity's start time. Works with Insta360, GoPro, phone, or any camera.

## Scope for v1

- **Telemetry input:** GPX only (Strava and Garmin both export GPX). FIT is out of scope.
- **Cutting:** frame-accurate re-encode by default; `-c copy` (keyframe-snapped) is
  an optional `cut_mode="copy"` config toggle.
- **Final assembly:** re-encode the concatenation of intro + highlights so codec/
  resolution/framerate match reliably across any source camera.
- **Tests:** `pytest`, focused on the sync logic (`test_sync.py`).

## Pipeline & data flow

```
main.py (argparse CLI)
   │  video.mp4, ride.gpx, music.mp3, flags
   ▼
Stage 1 — highlight_detector.py
   ├─ gpx_loader:  gpxpy → per-second speed/elevation/coords + activity start (UTC)
   ├─ video_probe: ffprobe → creation timestamp + duration/fps
   ├─ sync:        offset = video_start − activity_start → align timelines   ← tested
   ├─ optical_flow: OpenCV Farneback, sampled per-second → motion magnitude/chaos
   └─ scorer:      normalize + weight (GPS speed, flow) → per-second interest score
                   → filter (min speed, min score) → merge into segments
   ▼  segments [(start, end, score)]      (── --dry-run stops here, prints table ──)
Stage 2 — video_editor.py
   ├─ cut each segment (re-encode by default; copy toggle)
   ├─ concat segments
   └─ audio: FFmpeg amix (original @ low vol + music), music fade-out at end
   ▼
Stage 3 — intro_generator.py
   ├─ stats from GPX (distance, elevation gain, moving time)
   ├─ reverse-geocode first coord (geopy / Nominatim)
   ├─ optional: strava_client / garmin_client enrich stats
   └─ MoviePy → 7 s intro clip
   ▼
Final assembly: intro + highlights → re-encode concat → output.mp4
```

## Modules & responsibilities

| Module | Purpose | Key deps |
|--------|---------|----------|
| `main.py` | CLI, orchestration, wire flags → config, `--dry-run` | argparse |
| `config.py` | Dataclass of thresholds: `min_speed_kmh=3`, `score_cutoff`, GPS/flow weights, `original_audio_volume`, `music_volume`, music `fade_out_seconds`, `intro_duration=7`, `cut_mode` (`"reencode"`/`"copy"`) | — |
| `highlight_detector.py` | GPX parse, ffprobe, **sync offset**, optical flow, scoring, segment merge | gpxpy, opencv-python, numpy, ffmpeg-python |
| `video_editor.py` | FFmpeg cut / concat / audio-mix via subprocess | ffmpeg (subprocess) |
| `intro_generator.py` | Stats + reverse-geocode + MoviePy intro render | moviepy, geopy |
| `strava_client.py` | OAuth2 + token refresh (`.strava_token.json`), fetch activity stats | requests |
| `garmin_client.py` | Garmin Connect login, HR/power fetch | garminconnect |
| `test_sync.py` | Synthetic GPX fixtures → validate timeline alignment | pytest |

Each module has one clear purpose and a small public surface so it can be tested
and reasoned about independently.

## Sync logic (the tested core)

- **Activity start** = timestamp of first GPX trackpoint (tz-aware UTC).
- **Video start** = ffprobe `format.tags.creation_time` (UTC). Fallback: file
  mtime, with a clear warning that alignment may be approximate.
- `offset_seconds = video_start − activity_start`. A video-time `t` maps to
  activity-time `t + offset_seconds`; the GPS speed at that instant is read from
  the per-second series.
- `test_sync.py` builds a synthetic GPX (known start, known speed ramp) plus a
  fake video creation timestamp and asserts the mapping selects the correct
  per-second speed, covering: positive offset (video started after activity),
  negative offset (video started before), and out-of-range clamping.

## Interest scoring (Stage 1)

- Per-second GPS speed (km/h) and per-second optical-flow features
  (mean magnitude = intensity; direction variance = "chaos").
- Normalize each signal, combine with configurable weights into an interest score.
- Filter out: standstill (`speed < min_speed_kmh`), boring road (smooth flow +
  sustained speed), and any second scoring below `score_cutoff`.
- Merge surviving seconds into contiguous segments (with small gap-bridging so a
  one-second dip doesn't fragment a run).

## Error handling & optionality

- **FFmpeg check** at startup (`shutil.which` + `ffmpeg -version`); missing →
  clear error instructing `brew install ffmpeg`.
- **Strava/Garmin** are optional: skipped silently when no `.strava_token.json` /
  no Garmin credentials are configured. Any API failure logs a warning and the
  pipeline continues with GPX-only stats.
- **Geocoding:** Nominatim failure → fall back to a raw `lat, lon` string.
- **Missing `creation_time`** tag → mtime fallback with warning (see Sync).

## Constraints & environment

- All processing local; the only network calls are optional Strava/Garmin/Nominatim.
- FFmpeg installed via Homebrew.
- **Python 3.14** on this machine is new; `moviepy`/`opencv-python` wheels may lag.
  requirements.txt pins conservative versions; README recommends a venv and notes
  that if a wheel is unavailable the user may need Python 3.11–3.12. The sync test
  depends only on light packages so it runs regardless.

## Deliverables

`main.py`, `highlight_detector.py`, `video_editor.py`, `intro_generator.py`,
`strava_client.py`, `garmin_client.py`, `config.py`, `test_sync.py`,
`requirements.txt` (pinned), `README.md` (FFmpeg via Homebrew, Strava API app
registration, Garmin credentials setup), `.gitignore` (venv, tokens, outputs).
