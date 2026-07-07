# Adaptive music bed Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a music bed that fits the exact final-video length — play the track from the start, loop a seamless (auto-detected) region to fill the middle, end with the track's natural outro (crossfaded), and mix it under the whole video.

**Architecture:** `music_loop.detect_loop` finds a seamless loop region (numpy over decoded PCM); `music_bed.build_music_bed` assembles head+loop-fill+outro via FFmpeg to an exact duration; music moves from the reel stage to `build_final_video`, which builds the bed at the concat's duration and mixes it under the whole video.

**Tech Stack:** Python 3.11–3.14, numpy, FFmpeg (atrim/stream_loop/acrossfade/afade/amix), pytest. Reference: the user-approved `scratchpad/music_proto.py`.

## Global Constraints

- Bed = `head[0:loop_end]` + `loop[loop_start:loop_end]` repeated + natural outro (last `music_outro_seconds`) via `music_crossfade_seconds` crossfade + fade at the exact end; short video (`total < loop_end + outro_len - crossfade`) → `music[0:total]` + fade.
- Loop region auto-detected (early, seamless, ≥ `music_min_loop_seconds`), overridable with `--music-loop-start`/`--music-loop-end` (both must be given to override, else detect).
- Music spans the whole video: applied in `build_final_video`, NOT the reel stage. Reel builders no longer mix music.
- No `--music` → no bed, no mixing (unchanged). Music build/mix failure → warn and fall back to no music (video still renders).
- Config: `music_min_loop_seconds=8.0`, `music_outro_seconds=10.0`, `music_crossfade_seconds=1.5`; reuse `music_volume`, `original_audio_volume`, `fade_out_seconds`.
- Tests use pytest; run via `.venv/bin/python`. Venv at `.venv` (Python 3.14.5) has all deps; do NOT recreate it.
- All commits end with: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

---

## Existing interfaces

- `highlight_detector.get_video_duration(path) -> float`.
- `video_editor.py`: `build_highlight_reel(video_path, segments, music_path, cfg)`, `build_segment_reel(video_path, clips, music_path, cfg, gpx=None, offset_seconds=0.0, telemetry_source=None)`, `_concat_and_music(part_paths, music_path, reel_duration, workdir, cfg)` (concats; mixes music only when `music_path` given), `_run_ffmpeg_progress(cmd, total_seconds, label)`.
- `intro_generator.build_final_video(reel_path, gpx, cfg, output, args, offset_seconds=0.0)` — builds intro, then `_concat_intro_and_reel(intro, reel_path, cfg.intro_duration, output, W, H)`; try/finally rmtree workdir.
- `main.py`: segment path calls `build_segment_reel(args.video, clips, args.music, cfg, gpx=..., offset_seconds=..., telemetry_source=...)`; flow path calls `build_highlight_reel(args.video, segments, args.music, cfg)`; both then `build_final_video(reel, gpx, cfg, args.output, args, offset_seconds=r.offset_used)`. `build_parser()` has `--music`.
- `config.py`: `Config` with `music_volume`, `original_audio_volume`, `fade_out_seconds`.

---

## Task 1: music_loop.py — decode + seamless-loop detection

**Files:** Create `music_loop.py`; Test `test_sync.py`.

**Interfaces:**
- `_decode_pcm(path, sr=22050) -> numpy.ndarray` (mono float32).
- `detect_loop(path, min_loop_seconds, sr=22050, a_max_s=60.0, max_loop_s=25.0) -> tuple[float, float]` (loop_start, loop_end in seconds).

- [ ] **Step 1: Failing tests** — append to `test_sync.py`:

```python
import shutil as _sh6


@pytest.mark.skipif(_sh6.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_decode_pcm_length(tmp_path):
    import subprocess, music_loop
    src = tmp_path / "t.mp3"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=440:d=2",
                    str(src)], check=True, capture_output=True)
    x = music_loop._decode_pcm(str(src), sr=22050)
    assert x.dtype.name == "float32" and abs(len(x) - 2 * 22050) < 22050  # ~2s @ 22050


@pytest.mark.skipif(_sh6.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_detect_loop_on_periodic_tone(tmp_path):
    import subprocess, music_loop
    # 40s steady sine -> highly self-similar; a >=8s seamless loop should be found
    src = tmp_path / "tone.mp3"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=200:d=40",
                    str(src)], check=True, capture_output=True)
    ls, le = music_loop.detect_loop(str(src), min_loop_seconds=8.0)
    assert le - ls >= 8.0 - 1e-6
    assert 5.0 <= ls and le <= 40.0


@pytest.mark.skipif(_sh6.which("ffmpeg") is None, reason="ffmpeg not installed")
def test_detect_loop_short_track_fallback(tmp_path):
    import subprocess, music_loop
    src = tmp_path / "short.mp3"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=200:d=3",
                    str(src)], check=True, capture_output=True)
    ls, le = music_loop.detect_loop(str(src), min_loop_seconds=8.0)
    assert ls == 0.0 and le > 0.0     # too short -> whole-track fallback
```

- [ ] **Step 2: Run — FAIL** (`ModuleNotFoundError: music_loop`):
`.venv/bin/python -m pytest test_sync.py -k "decode_pcm or detect_loop" -v`

- [ ] **Step 3: Implement `music_loop.py`:**

```python
from __future__ import annotations

import subprocess

import numpy as np


def _decode_pcm(path: str, sr: int = 22050) -> np.ndarray:
    """Decode an audio file to a mono float32 numpy array at `sr` Hz."""
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-ac", "1", "-ar", str(sr),
         "-f", "f32le", "-"],
        capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.float32)


def detect_loop(path: str, min_loop_seconds: float, sr: int = 22050,
                a_max_s: float = 60.0, max_loop_s: float = 25.0):
    """Find an early, seamless loop (loop_start, loop_end) in seconds.

    Searches a coarse grid for a loop whose pre-window before loop_end best matches
    the pre-window before loop_start (smooth wrap), snapping to zero crossings.
    Falls back to (0.0, duration) when the track is too short for a loop."""
    x = _decode_pcm(path, sr)
    n = len(x)
    duration = n / sr
    win = int(0.12 * sr)
    hop = int(0.5 * sr)
    head = int(5.0 * sr)
    tail = int(1.0 * sr)
    a_max = int(a_max_s * sr)
    if n < head + int(min_loop_seconds * sr) + tail:
        return 0.0, float(duration)

    best = (None, None, 1e30)
    a = head
    while a + int(min_loop_seconds * sr) < n - tail and a <= a_max:
        wa = x[a - win:a]
        ea = float(np.sqrt(np.sum(wa * wa))) + 1e-9
        b = a + int(min_loop_seconds * sr)
        b1 = min(a + int(max_loop_s * sr), n - tail)
        while b <= b1:
            wb = x[b - win:b]
            diff = wb - wa
            seam = float(np.sqrt(np.sum(diff * diff))) / (ea + float(np.sqrt(np.sum(wb * wb))) + 1e-9)
            if seam < best[2]:
                best = (a, b, seam)
            b += hop
        a += hop
    a, b, _ = best
    if a is None:
        return 0.0, float(duration)

    def snap(i):
        lo = max(1, i - win); hi = min(n - 1, i + win)
        zc = [j for j in range(lo, hi) if x[j - 1] <= 0 < x[j]]
        return min(zc, key=lambda j: abs(j - i)) if zc else i

    return snap(a) / sr, snap(b) / sr
```

- [ ] **Step 4: Run — PASS.** **Step 5: Full suite** (`.venv/bin/python -m pytest test_sync.py -q`, was 111 → 114). **Step 6: Commit** `feat: music_loop — decode + seamless-loop detection`.

---

## Task 2: music_bed.py — assemble the bed to an exact length

**Files:** Create `music_bed.py`; Test `test_sync.py`.

**Interfaces:** `build_music_bed(music_path, total_duration, loop_start, loop_end, out_path, cfg) -> str`.

- [ ] **Step 1: Failing tests** — append to `test_sync.py`:

```python
@pytest.mark.skipif(_sh6.which("ffmpeg") is None or _sh6.which("ffprobe") is None,
                    reason="ffmpeg not installed")
def test_build_music_bed_long_exact_duration(tmp_path):
    import subprocess, music_bed
    from config import Config
    src = tmp_path / "m.mp3"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=220:d=40",
                    str(src)], check=True, capture_output=True)
    out = tmp_path / "bed.m4a"
    cfg = Config(); cfg.music_outro_seconds = 6.0; cfg.music_crossfade_seconds = 1.0
    # loop [10,25], target 60 -> needs head+loop-fill+outro
    music_bed.build_music_bed(str(src), 60.0, 10.0, 25.0, str(out), cfg)
    d = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                              "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout)
    assert abs(d - 60.0) < 0.3


@pytest.mark.skipif(_sh6.which("ffmpeg") is None or _sh6.which("ffprobe") is None,
                    reason="ffmpeg not installed")
def test_build_music_bed_short_linear(tmp_path):
    import subprocess, music_bed
    from config import Config
    src = tmp_path / "m.mp3"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=220:d=40",
                    str(src)], check=True, capture_output=True)
    out = tmp_path / "bed.m4a"
    cfg = Config()
    # target 8 < loop_end(25)+outro-xf -> linear branch
    music_bed.build_music_bed(str(src), 8.0, 10.0, 25.0, str(out), cfg)
    d = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                              "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout)
    assert abs(d - 8.0) < 0.3
```

- [ ] **Step 2: Run — FAIL** (`ModuleNotFoundError: music_bed`):
`.venv/bin/python -m pytest test_sync.py -k build_music_bed -v`

- [ ] **Step 3: Implement `music_bed.py`:**

```python
from __future__ import annotations

import os
import subprocess
import tempfile

from highlight_detector import get_video_duration


def _run(cmd):
    subprocess.run(cmd, check=True, capture_output=True)


def build_music_bed(music_path, total_duration, loop_start, loop_end, out_path, cfg) -> str:
    """Assemble a music bed of exactly total_duration: head + looped middle + natural
    outro (crossfaded) + fade; or a plain [0:total]+fade when the video is short."""
    track_dur = get_video_duration(music_path)
    fade = cfg.fade_out_seconds
    outro_len = min(cfg.music_outro_seconds, track_dur / 2.0)
    xf = cfg.music_crossfade_seconds

    # Short target: no room for head+outro -> linear play from the start + fade.
    if total_duration < loop_end + outro_len - xf:
        fstart = max(0.0, total_duration - fade)
        _run(["ffmpeg", "-y", "-v", "error", "-i", music_path, "-t", str(total_duration),
              "-af", f"afade=t=out:st={fstart}:d={fade}", out_path])
        return out_path

    workdir = tempfile.mkdtemp(prefix="rhe_bed_")
    try:
        head = os.path.join(workdir, "head.wav")
        loop = os.path.join(workdir, "loop.wav")
        fill = os.path.join(workdir, "fill.wav")
        headfill = os.path.join(workdir, "headfill.wav")
        outro = os.path.join(workdir, "outro.wav")
        fill_len = total_duration - outro_len + xf - loop_end

        _run(["ffmpeg", "-y", "-v", "error", "-i", music_path, "-ss", "0", "-to", str(loop_end), head])
        _run(["ffmpeg", "-y", "-v", "error", "-i", music_path, "-ss", str(loop_start), "-to", str(loop_end), loop])
        _run(["ffmpeg", "-y", "-v", "error", "-stream_loop", "-1", "-i", loop, "-t", str(fill_len), fill])
        _run(["ffmpeg", "-y", "-v", "error", "-i", music_path, "-ss", str(track_dur - outro_len), outro])
        listf = os.path.join(workdir, "hf.txt")
        with open(listf, "w") as f:
            f.write(f"file '{head}'\nfile '{fill}'\n")
        _run(["ffmpeg", "-y", "-v", "error", "-f", "concat", "-safe", "0", "-i", listf, "-c", "copy", headfill])
        fstart = max(0.0, total_duration - fade)
        _run(["ffmpeg", "-y", "-v", "error", "-i", headfill, "-i", outro,
              "-filter_complex",
              f"[0][1]acrossfade=d={xf}[m];[m]atrim=0:{total_duration},"
              f"afade=t=out:st={fstart}:d={fade}[out]",
              "-map", "[out]", out_path])
        return out_path
    finally:
        import shutil
        shutil.rmtree(workdir, ignore_errors=True)
```

- [ ] **Step 4: Run — PASS** (`-k build_music_bed`). **Step 5: Full suite** (116). **Step 6: Commit** `feat: music_bed — assemble head+loop+outro to an exact length`.

---

## Task 3: config + CLI + final-stage music mixing (move music off the reel)

**Files:** Modify `config.py`, `main.py`, `video_editor.py`, `intro_generator.py`, `README.md`; Test `test_sync.py`.

**Interfaces:**
- `config`: `music_min_loop_seconds=8.0`, `music_outro_seconds=10.0`, `music_crossfade_seconds=1.5`.
- `main`: `--music-loop-start`/`--music-loop-end` (float, optional); passes `music_path=None` into the reel builders; `build_final_video` handles music.
- `intro_generator._apply_music(combined_path, output, args, cfg) -> str` and `build_final_video` invoking it when `args.music`.

- [ ] **Step 1: Add config fields** — in `config.py` `Config`:

```python
    # Adaptive music bed
    music_min_loop_seconds: float = 8.0
    music_outro_seconds: float = 10.0
    music_crossfade_seconds: float = 1.5
```

- [ ] **Step 2: Add CLI flags** — in `main.build_parser()`:

```python
    p.add_argument("--music-loop-start", type=float, default=None,
                   help="Manual music loop-in point (s); overrides auto-detection "
                        "(use with --music-loop-end).")
    p.add_argument("--music-loop-end", type=float, default=None,
                   help="Manual music loop-out point (s); overrides auto-detection.")
```

- [ ] **Step 3: Move music off the reel** — in `main.py`, change the reel calls to pass `None` for the music argument (music is applied in the final stage):
  - segment path: `build_segment_reel(args.video, clips, None, cfg, gpx=gpx, offset_seconds=r.offset_used, telemetry_source=telemetry_source)`
  - flow path: `build_highlight_reel(args.video, segments, None, cfg)`
  (Leave `build_*_reel`/`_concat_and_music` signatures unchanged; passing `None` makes `_concat_and_music` skip its music branch.)

- [ ] **Step 4: Failing test for the music-mix path** — append to `test_sync.py`:

```python
@pytest.mark.skipif(_sh6.which("ffmpeg") is None or _sh6.which("ffprobe") is None,
                    reason="ffmpeg not installed")
def test_build_final_video_with_music_has_audio(tmp_path, monkeypatch):
    import subprocess, intro_generator
    from datetime import datetime, timezone
    from config import Config
    from highlight_detector import GpxData
    reel = tmp_path / "reel.mp4"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=s=640x360:d=3",
                    "-f", "lavfi", "-i", "sine=d=3", "-c:v", "libx264", "-pix_fmt",
                    "yuv420p", "-c:a", "aac", "-shortest", str(reel)],
                   check=True, capture_output=True)
    music = tmp_path / "m.mp3"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=220:d=40",
                    str(music)], check=True, capture_output=True)
    monkeypatch.setattr(intro_generator, "reverse_geocode", lambda lat, lon: "T, NL")
    g = GpxData(start_time=datetime(2026, 7, 5, 12, 0, 0, tzinfo=timezone.utc),
                speeds_kmh=[10], coords=[(52.0, 4.0)], elevations_m=[5.0], hr_bpm=[100],
                cum_distance_m=[0.0], total_distance_km=5.0, elevation_gain_m=20.0,
                moving_time_s=600.0, first_coord=(52.0, 4.0), name="Rit")
    cfg = Config(); cfg.intro_duration = 2.0; cfg.intro_fps = 8
    class A:
        video = str(reel); strava = False; garmin = False; music = str(music)
        music_loop_start = 10.0; music_loop_end = 25.0
    out = tmp_path / "final.mp4"
    intro_generator.build_final_video(str(reel), g, cfg, str(out), A())
    codec = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "a:0",
                            "-show_entries", "stream=codec_type", "-of", "csv=p=0", str(out)],
                           capture_output=True, text=True).stdout.strip()
    assert out.exists() and codec == "audio"
```

- [ ] **Step 5: Run — FAIL** (build_final_video ignores music / no `_apply_music`):
`.venv/bin/python -m pytest test_sync.py -k build_final_video_with_music -v`

- [ ] **Step 6: Implement the music mix in `intro_generator.py`.** Add the helper:

```python
def _apply_music(combined_path: str, output: str, args, cfg: Config) -> str:
    """Build the adaptive music bed at the video's length and mix it under the audio.
    On any failure, fall back to the un-scored video."""
    import shutil
    from highlight_detector import get_video_duration
    from video_editor import _run_ffmpeg_progress
    try:
        import music_bed
        ls, le = args.music_loop_start, args.music_loop_end
        if ls is None or le is None:
            from music_loop import detect_loop
            ls, le = detect_loop(args.music, cfg.music_min_loop_seconds)
        total = get_video_duration(combined_path)
        workdir = tempfile.mkdtemp(prefix="rhe_music_")
        try:
            bed = music_bed.build_music_bed(args.music, total, ls, le,
                                            os.path.join(workdir, "bed.m4a"), cfg)
            cmd = ["ffmpeg", "-y", "-i", combined_path, "-i", bed,
                   "-filter_complex",
                   f"[0:a]volume={cfg.original_audio_volume}[a0];"
                   f"[1:a]volume={cfg.music_volume}[a1];"
                   f"[a0][a1]amix=inputs=2:duration=first[aout]",
                   "-map", "0:v", "-map", "[aout]",
                   "-c:v", "copy", "-c:a", "aac", output]
            _run_ffmpeg_progress(cmd, total, "Muziek mixen")
        finally:
            shutil.rmtree(workdir, ignore_errors=True)
    except Exception as e:
        print(f"[warn] muziek toevoegen mislukt ({e}); zonder muziek.")
        shutil.copy(combined_path, output)
    return output
```

Change `build_final_video` to route through it when music is given:

```python
def build_final_video(reel_path: str, gpx: GpxData, cfg: Config, output: str, args,
                      offset_seconds: float = 0.0) -> str:
    from highlight_detector import get_video_resolution
    sw, sh = get_video_resolution(args.video)
    W, H = _target_dims(sw, sh, cfg.output_height)
    workdir = tempfile.mkdtemp(prefix="rhe_final_")
    try:
        intro = os.path.join(workdir, "intro.mp4")
        extra = _gather_extra_stats(gpx, args)
        build_intro_clip(gpx, cfg, intro, extra, size=(W, H),
                         video_path=args.video, offset_seconds=offset_seconds,
                         gpx_path=getattr(args, "gpx", None))
        music = getattr(args, "music", None)
        target = os.path.join(workdir, "combined.mp4") if music else output
        _concat_intro_and_reel(intro, reel_path, cfg.intro_duration, target, W, H)
        if music:
            _apply_music(target, output, args, cfg)
        return output
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
```

- [ ] **Step 7: Run — PASS** (`-k build_final_video_with_music`). **Step 8: Full suite** (117) + `.venv/bin/python -c "import main, music_loop, music_bed, intro_generator"`.

- [ ] **Step 9: README** — update the music note:

```markdown
Pass `--music track.mp3` and the tool builds an adaptive bed the exact length of the
final video: it plays the track from the start, loops a seamless region (auto-detected,
or set `--music-loop-start`/`--music-loop-end`) to fill the middle, then ends with the
track's natural outro, mixed under the whole video (intro included).
```

- [ ] **Step 10: Commit** `feat: adaptive music bed mixed under the whole video`.

---

## Self-Review

**Spec coverage:**
- `_decode_pcm` + `detect_loop` (early seamless loop, zero-crossing snap, short-track fallback) → Task 1. ✓
- `build_music_bed` (head+loop-fill+outro crossfade + fade; short-video linear branch; exact duration) → Task 2. ✓
- config fields, `--music-loop-*`, music off the reel (pass None), `_apply_music` + `build_final_video` mixing under the whole video, README → Task 3. ✓
- Overrides > detect; no-music unchanged; failure falls back to no music → Tasks 2–3. ✓
- Tests: decode/detect (+fallback), bed duration (long + short), final video has audio → Tasks 1–3. ✓

**Placeholder scan:** No TBD/TODO; each code step is complete.

**Type consistency:** `detect_loop(path, min_loop_seconds, ...) -> (float,float)` (Task 1) consumed by `_apply_music` (Task 3). `build_music_bed(music_path, total_duration, loop_start, loop_end, out_path, cfg)` (Task 2) called by `_apply_music` (Task 3). `_apply_music(combined_path, output, args, cfg)` (Task 3) called by `build_final_video` (Task 3). config fields (Task 3) read by `build_music_bed` (Task 2: `music_outro_seconds`/`music_crossfade_seconds`/`fade_out_seconds`) and `_apply_music` (`music_min_loop_seconds`/`music_volume`/`original_audio_volume`). `get_video_duration`/`_run_ffmpeg_progress` reused. Reel builders keep their signatures; main passes `None` for music.
```
