# Output resolution + encode progress Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a configurable output resolution (`--output-height N|source`, default 1080) that drives the final assembly + intro, and a real percentage during the two heavy re-encodes (per-clip HUD overlay + final assembly).

**Architecture:** `build_final_video` computes the exact output `(W, H)` from the source aspect and the target height, renders the intro at that size (fonts scaled), and scales both intro and reel to that exact `W×H` for concat. A `_run_ffmpeg_progress` helper parses `ffmpeg -progress` to print a `\r` percentage on the two long encodes.

**Tech Stack:** Python 3.11–3.14, FFmpeg (`-progress`, `scale`), MoviePy (intro), pytest.

## Global Constraints

- `--output-height` accepts an int (default `1080`) or the literal `source` (case-insensitive) → the source video height.
- Output width follows the source aspect: `W = _even(round(source_w * H / source_h))`; both intro and reel are scaled to that exact `W×H` so concat dimensions always match.
- The HUD is unchanged (it already scales via `k = H/1080` at the source resolution).
- Encode `%` only for the two heavy re-encodes (per-clip HUD overlay + final assembly), via `ffmpeg -progress`, printed with the existing `_log` (stderr, `\r`). Fast `-c copy` concat/music steps keep their status line; the per-frame HUD-render `%` is unchanged.
- Tests use pytest; run via `.venv/bin/python`. Venv at `.venv` (Python 3.14.5) has all deps; do NOT recreate it.
- All commits end with: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

---

## Existing interfaces

- `config.py`: `Config` dataclass.
- `highlight_detector.py`: `_ffprobe_json(path) -> dict`, `get_video_duration(path) -> float`, `get_video_creation_time`.
- `main.py`: `build_config_from_args(args) -> Config`; `main()` builds `cfg`, loads gpx, resolves offset, then routes segment/flow render. `--output` (path) already exists.
- `intro_generator.py`: `build_intro_clip(gpx, cfg, out_path, extra_stats=None)` (renders 1920×1080, fontsize 70), `_concat_intro_and_reel(intro_path, reel_path, intro_duration, output)` (hard-codes `scale=1920:1080` for both inputs, uses an anullsrc silent-audio input), `build_final_video(reel_path, gpx, cfg, output, args)` (mkdtemp → build_intro_clip → _concat_intro_and_reel, try/finally rmtree).
- `video_editor.py`: `_log(msg, end="\n")` (stderr progress), `build_segment_reel(...)` — the per-clip HUD overlay encode is `subprocess.run(["ffmpeg","-y","-ss",str(clip.start),"-t",str(dur),"-i",video_path,"-framerate",str(cfg.hud_fps),"-i",pattern,"-filter_complex","[0:v][1:v]overlay=0:0:shortest=1[v]","-map","[v]","-map","0:a?","-c:v","libx264","-preset","veryfast","-c:a","aac",part], check=True, capture_output=True)` preceded by `_log(f"[{i+1}/{n}] {clip.name} — overlay encoderen…")`.

---

## Task 1: `--output-height` config, resolution probe + resolver

**Files:** Modify `config.py`, `highlight_detector.py`, `main.py`; Test `test_sync.py`.

**Interfaces:**
- `config.output_height: int = 1080`.
- `highlight_detector.get_video_resolution(path) -> tuple[int, int]` (width, height).
- `main.resolve_output_height(value, video_path) -> int` — `"source"` → source height; else `int(value)`.
- `main.build_parser` gains `--output-height` (str, default `"1080"`); `main()` sets `cfg.output_height = resolve_output_height(args.output_height, args.video)`.

- [ ] **Step 1: Failing tests** — append to `test_sync.py`:

```python
def test_get_video_resolution(monkeypatch):
    import highlight_detector
    monkeypatch.setattr(highlight_detector, "_ffprobe_json",
                        lambda path: {"streams": [{"codec_type": "audio"},
                                                  {"codec_type": "video", "width": 3840, "height": 2160}]})
    assert highlight_detector.get_video_resolution("x.mp4") == (3840, 2160)


def test_resolve_output_height_source(monkeypatch):
    import main, highlight_detector
    monkeypatch.setattr(highlight_detector, "get_video_resolution", lambda p: (3840, 2160))
    assert main.resolve_output_height("source", "x.mp4") == 2160
    assert main.resolve_output_height("SOURCE", "x.mp4") == 2160


def test_resolve_output_height_int():
    import main
    assert main.resolve_output_height("2160", "x.mp4") == 2160
    assert main.resolve_output_height("1080", "x.mp4") == 1080
```

- [ ] **Step 2: Run — FAIL** (`AttributeError`/`ImportError`):
`.venv/bin/python -m pytest test_sync.py -k "get_video_resolution or resolve_output_height" -v`

- [ ] **Step 3: Implement.**

`config.py` — add to `Config` (near the HUD fields):

```python
    output_height: int = 1080   # final video height; width follows source aspect
```

`highlight_detector.py` — add near the other ffprobe helpers:

```python
def get_video_resolution(path: str) -> tuple[int, int]:
    info = _ffprobe_json(path)
    vs = next(s for s in info.get("streams", []) if s.get("codec_type") == "video")
    return int(vs["width"]), int(vs["height"])
```

`main.py` — add the resolver (near `build_config_from_args`):

```python
def resolve_output_height(value, video_path) -> int:
    """Resolve --output-height: 'source' -> the source video height; else int."""
    if isinstance(value, str) and value.strip().lower() == "source":
        from highlight_detector import get_video_resolution
        return get_video_resolution(video_path)[1]
    return int(value)
```

In `build_parser()`, add:

```python
    p.add_argument("--output-height", default="1080",
                   help="Output video height in pixels (default 1080), or 'source' to "
                        "match the input. Width follows the source aspect. e.g. 2160 for 4K.")
```

In `main()`, right after `cfg = build_config_from_args(args)`:

```python
    cfg.output_height = resolve_output_height(args.output_height, args.video)
```

- [ ] **Step 4: Run — PASS.** **Step 5: Full suite** (`.venv/bin/python -m pytest test_sync.py -q`, was 81 → 84). Also `.venv/bin/python main.py --help` shows `--output-height`. **Step 6: Commit** `feat: --output-height config + source-resolution probe`.

---

## Task 2: Render final video + intro at the target resolution

**Files:** Modify `intro_generator.py`; Test `test_sync.py`.

**Interfaces:**
- `build_intro_clip(gpx, cfg, out_path, extra_stats=None, size=(1920, 1080))` — renders at `size`, fonts scaled by `H/1080`.
- `_concat_intro_and_reel(intro_path, reel_path, intro_duration, output, width, height)` — scales both inputs to `{width}:{height}`.
- `build_final_video` computes `(W, H)` from the source aspect + `cfg.output_height` and threads it through. New module helper `_even(x) -> int`.
- Consumes: `highlight_detector.get_video_resolution`.

- [ ] **Step 1: Failing test (real-ffmpeg, non-default height)** — append to `test_sync.py`:

```python
import shutil as _sh3


@pytest.mark.skipif(_sh3.which("ffmpeg") is None or _sh3.which("ffprobe") is None,
                    reason="ffmpeg not installed")
def test_final_video_respects_output_height(tmp_path, monkeypatch):
    import subprocess
    from datetime import datetime, timezone
    from config import Config
    from highlight_detector import GpxData
    import intro_generator
    # a 16:9 reel (also used as the aspect source)
    reel = tmp_path / "reel.mp4"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=s=640x360:d=2",
                    "-f", "lavfi", "-i", "sine=d=2", "-c:v", "libx264", "-pix_fmt",
                    "yuv420p", "-c:a", "aac", "-shortest", str(reel)],
                   check=True, capture_output=True)
    monkeypatch.setattr(intro_generator, "reverse_geocode", lambda lat, lon: "Test, NL")
    g = GpxData(start_time=datetime(2026, 7, 5, 12, 0, 0, tzinfo=timezone.utc),
                speeds_kmh=[10], coords=[(52.0, 4.0)], elevations_m=[5.0], hr_bpm=[100],
                cum_distance_m=[0.0], total_distance_km=5.0, elevation_gain_m=20.0,
                moving_time_s=600.0, first_coord=(52.0, 4.0))
    cfg = Config(); cfg.output_height = 720
    class A:
        video = str(reel); strava = False; garmin = False
    out = tmp_path / "final.mp4"
    intro_generator.build_final_video(str(reel), g, cfg, str(out), A())
    h = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0",
                        "-show_entries", "stream=height", "-of", "csv=p=0", str(out)],
                       capture_output=True, text=True).stdout.strip()
    assert h == "720"
```

- [ ] **Step 2: Run — FAIL** (output is 1080, not 720):
`.venv/bin/python -m pytest test_sync.py -k final_video_respects_output_height -v`

- [ ] **Step 3: Implement in `intro_generator.py`.**

Add the even-rounding helper near the top (after imports):

```python
def _even(x) -> int:
    """Round to an even integer (libx264 requires even dimensions)."""
    x = int(round(x))
    return x - (x % 2)
```

Change `build_intro_clip` to accept a `size` and scale the font:

```python
def build_intro_clip(gpx: GpxData, cfg: Config, out_path: str,
                     extra_stats: dict | None = None, size=(1920, 1080)) -> str:
    """Render a stats intro clip with MoviePy at the given size."""
    from moviepy.editor import TextClip, ColorClip, CompositeVideoClip

    location = reverse_geocode(*gpx.first_coord)
    date_str = gpx.start_time.strftime("%d %B %Y")
    lines = [
        location,
        date_str,
        f"{gpx.total_distance_km:.1f} km   +{gpx.elevation_gain_m:.0f} m",
        f"Moving time {format_moving_time(gpx.moving_time_s)}",
    ]
    if extra_stats:
        if extra_stats.get("avg_hr"):
            lines.append(f"Avg HR {extra_stats['avg_hr']} bpm")
        if extra_stats.get("avg_power"):
            lines.append(f"Avg Power {extra_stats['avg_power']} W")

    W, H = size
    fontsize = max(12, int(round(70 * H / 1080)))
    bg = ColorClip(size=(W, H), color=(15, 15, 20)).set_duration(cfg.intro_duration)
    text = "\n".join(lines)
    txt = (TextClip(text, fontsize=fontsize, color="white", font="Arial", method="label")
           .set_duration(cfg.intro_duration)
           .set_position("center"))
    clip = CompositeVideoClip([bg, txt]).set_duration(cfg.intro_duration)
    clip.write_videofile(out_path, fps=30, codec="libx264", audio=False, logger=None)
    return out_path
```

Change `_concat_intro_and_reel` to take + use `width, height`:

```python
def _concat_intro_and_reel(intro_path: str, reel_path: str, intro_duration: float,
                           output: str, width: int, height: int) -> str:
    """Concat a silent, video-only intro with the reel at width×height, giving the
    intro a real silent audio stream via an anullsrc input so concat's pads balance."""
    subprocess.run(
        ["ffmpeg", "-y", "-i", intro_path, "-i", reel_path,
         "-f", "lavfi", "-t", str(intro_duration), "-i", "anullsrc=r=44100:cl=stereo",
         "-filter_complex",
         f"[0:v]scale={width}:{height},setsar=1,fps=30[v0];"
         f"[1:v]scale={width}:{height},setsar=1,fps=30[v1];"
         "[v0][2:a][v1][1:a]concat=n=2:v=1:a=1[v][a]",
         "-map", "[v]", "-map", "[a]",
         "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", output],
        check=True, capture_output=True,
    )
    return output
```

Change `build_final_video` to compute the target size and thread it:

```python
def build_final_video(reel_path: str, gpx: GpxData, cfg: Config, output: str, args) -> str:
    """Prepend the intro to the reel and re-encode at the configured resolution."""
    from highlight_detector import get_video_resolution
    sw, sh = get_video_resolution(args.video)
    H = cfg.output_height
    W = _even(sw * H / sh)
    workdir = tempfile.mkdtemp(prefix="rhe_final_")
    try:
        intro = os.path.join(workdir, "intro.mp4")
        extra = _gather_extra_stats(gpx, args)
        build_intro_clip(gpx, cfg, intro, extra, size=(W, H))
        return _concat_intro_and_reel(intro, reel_path, cfg.intro_duration, output, W, H)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
```

- [ ] **Step 4: Run — PASS.** **Step 5: Full suite** (85). **Step 6: Commit** `feat: render final video + intro at the configured output resolution`.

---

## Task 3: Encode progress on the two heavy re-encodes

**Files:** Modify `video_editor.py`, `intro_generator.py`; Test `test_sync.py`.

**Interfaces:**
- `video_editor._run_ffmpeg_progress(cmd, total_seconds, label) -> None` — runs `cmd` (an `ffmpeg -y ...` arg list) with `-progress pipe:1 -nostats`, prints `\r{label} — {pct}%` via `_log`, raises `RuntimeError` on non-zero exit.
- Applied to: the per-clip HUD overlay encode in `build_segment_reel`; the final-assembly encode in `intro_generator._concat_intro_and_reel`.

- [ ] **Step 1: Failing test (real-ffmpeg smoke)** — append to `test_sync.py`:

```python
import shutil as _sh4


@pytest.mark.skipif(_sh4.which("ffmpeg") is None,
                    reason="ffmpeg not installed")
def test_run_ffmpeg_progress_encodes(tmp_path):
    import subprocess, video_editor
    src = tmp_path / "s.mp4"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=s=320x240:d=2",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(src)],
                   check=True, capture_output=True)
    out = tmp_path / "o.mp4"
    cmd = ["ffmpeg", "-y", "-i", str(src), "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out)]
    video_editor._run_ffmpeg_progress(cmd, 2.0, "test-encode")   # must not raise
    assert out.exists() and out.stat().st_size > 0


@pytest.mark.skipif(_sh4.which("ffmpeg") is None,
                    reason="ffmpeg not installed")
def test_run_ffmpeg_progress_raises_on_failure(tmp_path):
    import video_editor, pytest
    cmd = ["ffmpeg", "-y", "-i", str(tmp_path / "nope.mp4"), str(tmp_path / "o.mp4")]
    with pytest.raises(RuntimeError):
        video_editor._run_ffmpeg_progress(cmd, 1.0, "bad")
```

- [ ] **Step 2: Run — FAIL** (`AttributeError: _run_ffmpeg_progress`):
`.venv/bin/python -m pytest test_sync.py -k run_ffmpeg_progress -v`

- [ ] **Step 3: Implement in `video_editor.py`** (near `_log`):

```python
def _run_ffmpeg_progress(cmd, total_seconds, label):
    """Run an ffmpeg command, printing a \\r percentage parsed from -progress output.
    Raises RuntimeError on a non-zero exit (like check=True)."""
    # Inject the progress/nostats global options right after the 'ffmpeg' program.
    full = [cmd[0], "-nostats", "-progress", "pipe:1"] + cmd[1:]
    with tempfile.TemporaryFile(mode="w+") as errf:
        proc = subprocess.Popen(full, stdout=subprocess.PIPE, stderr=errf, text=True)
        for line in proc.stdout:
            line = line.strip()
            if line.startswith("out_time_us=") and total_seconds > 0:
                raw = line.split("=", 1)[1]
                if raw.isdigit():
                    pct = min(100.0, int(raw) / 1_000_000 / total_seconds * 100)
                    _log(f"\r{label} — {pct:3.0f}%", end="")
        proc.wait()
        _log("")  # end the \r line
        if proc.returncode != 0:
            errf.seek(0)
            raise RuntimeError(f"ffmpeg failed ({label}):\n{errf.read()[-800:]}")
```

Replace the per-clip HUD overlay `subprocess.run(...)` in `build_segment_reel` with a progress-tracked call. Change the existing `_log(f"[{i+1}/{n}] {clip.name} — overlay encoderen…")` + `subprocess.run([...overlay...], check=True, capture_output=True)` to:

```python
                overlay_cmd = [
                    "ffmpeg", "-y", "-ss", str(clip.start), "-t", str(dur), "-i", video_path,
                    "-framerate", str(cfg.hud_fps), "-i", pattern,
                    "-filter_complex", "[0:v][1:v]overlay=0:0:shortest=1[v]",
                    "-map", "[v]", "-map", "0:a?",
                    "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", part]
                _run_ffmpeg_progress(overlay_cmd, dur, f"[{i + 1}/{n}] {clip.name} — overlay encoderen")
```

(Remove the now-redundant `_log(... overlay encoderen…)` line that preceded it, since the progress call prints its own label; keep the `part_paths.append(part); continue` and the `except` fallback intact.)

- [ ] **Step 4: Apply to the final assembly in `intro_generator._concat_intro_and_reel`.** Replace its `subprocess.run(cmd, check=True, capture_output=True)` with a progress-tracked call whose total is the intro + reel duration:

```python
def _concat_intro_and_reel(intro_path: str, reel_path: str, intro_duration: float,
                           output: str, width: int, height: int) -> str:
    from video_editor import _run_ffmpeg_progress
    from highlight_detector import get_video_duration
    cmd = ["ffmpeg", "-y", "-i", intro_path, "-i", reel_path,
           "-f", "lavfi", "-t", str(intro_duration), "-i", "anullsrc=r=44100:cl=stereo",
           "-filter_complex",
           f"[0:v]scale={width}:{height},setsar=1,fps=30[v0];"
           f"[1:v]scale={width}:{height},setsar=1,fps=30[v1];"
           "[v0][2:a][v1][1:a]concat=n=2:v=1:a=1[v][a]",
           "-map", "[v]", "-map", "[a]",
           "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", output]
    total = intro_duration + get_video_duration(reel_path)
    _run_ffmpeg_progress(cmd, total, "Eindmontage")
    return output
```

(The lazy imports avoid any import-cycle risk: `video_editor` does not import `intro_generator`.)

- [ ] **Step 5: Run the smoke tests — PASS** (not skipped):
`.venv/bin/python -m pytest test_sync.py -k run_ffmpeg_progress -v`

- [ ] **Step 6: Full suite + import check.**
`.venv/bin/python -m pytest test_sync.py -q` (86) and `.venv/bin/python -c "import main, video_editor, intro_generator"`.

- [ ] **Step 7: README** — add a line under the HUD/usage section:

```markdown
Output resolution defaults to 1080p. Render at the source resolution (e.g. 4K) with
`--output-height source` (or a specific height like `--output-height 1440`); width
follows the source aspect and the HUD scales with it. The two heavy encodes
(per-clip overlay and final assembly) show a live percentage.
```

- [ ] **Step 8: Commit** `feat: live percentage on the per-clip overlay and final-assembly encodes`.

---

## Self-Review

**Spec coverage:**
- `output_height` config + `--output-height N|source` + `get_video_resolution` + `resolve_output_height` + main wiring → Task 1. ✓
- Target `(W,H)` from source aspect, both scaled to exact `W×H`, intro rendered at `(W,H)` with scaled fonts → Task 2. ✓
- `_run_ffmpeg_progress` (parse `out_time_us`, `\r` %, raise on failure) applied to per-clip overlay + final assembly; concat/music keep status lines; HUD frame % unchanged → Task 3. ✓
- Tests: resolve/parse, real-ffmpeg output-height=720, progress smoke + failure-raises → Tasks 1–3. ✓
- README → Task 3. ✓
- HUD untouched (scales via H/1080) → honored. ✓

**Placeholder scan:** No TBD/TODO; every code step is complete.

**Type consistency:** `get_video_resolution(path) -> (w,h)` (Task 1) consumed by `resolve_output_height` (Task 1) and `build_final_video` (Task 2). `cfg.output_height` (Task 1) read by `build_final_video` (Task 2). `build_intro_clip(..., size=(W,H))` (Task 2) called by `build_final_video` (Task 2). `_concat_intro_and_reel(intro, reel, intro_duration, output, width, height)` (Task 2) — the same 6-arg signature is used in Task 3's rewrite and by `build_final_video`. `_run_ffmpeg_progress(cmd, total_seconds, label)` (Task 3) called from `build_segment_reel` (video_editor) and `_concat_intro_and_reel` (intro_generator, lazy import).
