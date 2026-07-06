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

## Optional: Strava stats

1. Create an API app at https://www.strava.com/settings/api → note Client ID/Secret.
2. Complete the OAuth2 flow once and save the tokens to `.strava_token.json`:

   ```json
   {"access_token": "...", "refresh_token": "...", "expires_at": 0}
   ```

3. Export credentials and run with `--strava`:

   ```bash
   export STRAVA_CLIENT_ID=xxxxx
   export STRAVA_CLIENT_SECRET=xxxxx
   python main.py --video ride.mp4 --gpx ride.gpx --strava
   ```

Tokens refresh automatically. `.strava_token.json` is gitignored.

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
