# ride-highlight-editor

Automatically generate highlight videos from mountainbike action-cam footage,
using GPS/telemetry from a Strava or Garmin **GPX export** synced to the video
timeline via the video's creation timestamp. Camera-agnostic (Insta360, GoPro,
phone, anything).

## Requirements

- macOS with Python 3.11–3.14
- FFmpeg via Homebrew:

  ```bash
  brew install ffmpeg
  ```

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

> Python 3.14 is new; if `opencv-python`/`moviepy` wheels are unavailable for
> your interpreter, use Python 3.11 or 3.12 in the venv.

## Usage

```bash
# Dry run — print detected segments and scores, render nothing:
python main.py --video ride.mp4 --gpx ride.gpx --dry-run

# Full render with music:
python main.py --video ride.mp4 --gpx ride.gpx --music track.mp3 --output highlight.mp4

# Frame-accurate (default) vs fast keyframe copy:
python main.py --video ride.mp4 --gpx ride.gpx --cut-mode copy
```

Pass `--music track.mp3` and the tool builds an adaptive bed the exact length of the
final video: it plays the track from the start, loops a seamless region (auto-detected,
or set `--music-loop-start`/`--music-loop-end`) to fill the middle, then ends with the
track's natural outro, mixed under the whole video (intro included).

### Multiple video files

`--video` accepts several files that jointly cover one ride:
`--video clip1.mp4 clip2.mp4 clip3.mp4`. Files are placed on the ride by their recording
time, and their relative spacing (same camera) is trusted. `--sync-offset` **anchors the
earliest-recorded file** at that offset — the same absolute meaning it has for a single
file — and the other files follow by their recording-time deltas, so one value corrects a
constant camera-clock skew across all of them. The segment/highlight selection runs on
the ride as usual; only ride portions that actually have footage are rendered (uncovered
segments are dropped with a warning), and `--pick` numbers just the covered segments. One
`--video` file behaves exactly as before.

Tunable flags: `--min-speed`, `--score-cutoff`, `--cut-mode`.
Deeper tuning lives in `config.py`.

#### Building the track from Strava

`--gpx` is optional: with `--strava --strava-activity-id <id>` the ride track is
built automatically from the Strava activity (its GPS/altitude/HR streams), so you can
render from Strava alone. A supplied `--gpx` always takes priority.

### Highlight modes

By default (`--mode auto`) the tool uses your **Strava segments** as highlights when
available, otherwise it falls back to speed/motion detection.

```bash
# Segment mode: one clip per noteworthy segment (medal/PR/starred) with a
# lower-third summary (name · time · speed · power · HR). Needs Strava + activity id:
python main.py --video ride.mp4 --gpx ride.gpx --music track.mp3 \
    --strava --strava-activity-id 1234567890 --mode segments

# Force the classic speed/motion mode:
python main.py --video ride.mp4 --gpx ride.gpx --mode flow
```

Segment timing uses the same sync offset as the rest of the pipeline
(`--inspect` / `--auto-sync` / `--sync-offset`). The filename timestamp
(`VID_YYYYMMDD_HHMMSS`) is used as a sync source when `creation_time` metadata is
missing.

In segment mode each clip carries an animated telemetry **HUD** (segment name + heart
rate, elevation + slope, a speedometer, and a segment minimap with a live position
dot), drawn from the GPX per-second data. Disable it with `hud_enabled = False` in
`config.py` to fall back to a simple lower-third caption. HUD frames render at
`hud_fps` (default 15) and the speedometer scales to `speedo_max_kmh` (default 45).

When Strava is configured and an activity id is given, the HUD is fed by Strava's
per-second **streams** (smoothed speed, heart rate, power, grade) rather than
values computed from the GPX — more accurate, and it adds a power (W) readout.
Missing streams fall back to the GPX data per field.

Output resolution defaults to 1080p. Render at the source resolution (e.g. 4K) with
`--output-height source` (or a specific height like `--output-height 1440`); width
follows the source aspect and the HUD scales with it. The two heavy encodes
(per-clip overlay and final assembly) show a live percentage.

### Intro

The intro is an animated title card: the curviest clip of the ride plays dimmed
behind a route map that draws itself in, with the route name, date, and stats
(distance, time, avg speed, elevation, and power when available). It renders at the
configured output resolution and its background clip follows the sync offset.

### Fixing sync (copied videos with wrong timestamps)

If the video's `creation_time` was altered by copying, the GPX↔video alignment is
off and too much footage is kept. Diagnose and correct it:

```bash
# 1. Inspect: see the offset sources and aligned speed/motion sparklines (video time).
python main.py --video ride.mp4 --gpx ride.gpx --inspect

# 2a. Let cross-correlation pick the offset automatically for the render:
python main.py --video ride.mp4 --gpx ride.gpx --music track.mp3 --auto-sync

# 2b. Or set the offset by hand (video_t maps to activity_t + offset):
python main.py --video ride.mp4 --gpx ride.gpx --music track.mp3 --sync-offset 137.5
```

`--inspect` shows the metadata offset, the auto-aligned offset with its correlation
(confidence), and which one is selected. Precedence: `--sync-offset` > `--auto-sync`
> file timestamp. A low correlation warning means auto-align is uncertain — compare
the `speed` and `motion` sparklines (peaks should line up) and set `--sync-offset`.

## Optional: Strava stats

1. Create an API app at https://www.strava.com/settings/api. Fill in:
   - **Website:** anything (e.g. `http://localhost`)
   - **Authorization Callback Domain:** `localhost` (just the domain — no
     `http://`, no port, no path)

   Note the **Client ID** and **Client Secret**.

2. Export the credentials and run the one-time OAuth helper. It opens your
   browser, catches the redirect on `localhost`, and writes
   `.strava_token.json` for you:

   ```bash
   export STRAVA_CLIENT_ID=xxxxx
   export STRAVA_CLIENT_SECRET=xxxxx
   python strava_auth.py            # or: python strava_auth.py --port 8721
   ```

3. Run the pipeline with `--strava`:

   ```bash
   python main.py --video ride.mp4 --gpx ride.gpx --strava
   ```

Tokens refresh automatically after that. `.strava_token.json` is gitignored.

## Optional: Garmin Connect stats

```bash
export GARMIN_EMAIL="you@example.com"
export GARMIN_PASSWORD="your-password"
python main.py --video ride.mp4 --gpx ride.gpx --garmin
```

Both integrations are optional and skipped gracefully if unconfigured; API
failures log a warning and the pipeline continues with GPX-only stats.

## Tests

```bash
python -m pytest test_sync.py -v
```

## Troubleshooting

- **"ffmpeg not found"** → `brew install ffmpeg`.
- **Intro/reel concat audio error** → some intros are silent; if the final
  concat fails on the audio map, the intro can be given a silent track via
  `anullsrc` (see `intro_generator.build_final_video`).
- **Segments look off by a second or two** → the video's `creation_time` tag
  may be missing (falls back to file mtime). Prefer footage with intact metadata.
