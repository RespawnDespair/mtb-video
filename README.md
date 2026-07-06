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

Tunable flags: `--min-speed`, `--score-cutoff`, `--cut-mode`.
Deeper tuning lives in `config.py`.

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
