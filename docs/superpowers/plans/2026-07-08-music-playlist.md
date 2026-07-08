# Music playlist (folder of mp3s) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the loop-detection music bed with a simple playlist: `--music` points at a folder (or file); tracks play back-to-back with short crossfades, the list repeats to fill the video, and it fades out at the exact end.

**Architecture:** New `music_playlist.py` (`gather_tracks` + `build_music_bed`) replaces `music_loop.py` + `music_bed.py`. `intro_generator._apply_music` calls it; the existing feature-#2 mixing (bed under the whole video) is unchanged. The loop CLI flags and config fields are removed.

**Tech Stack:** Python 3.14 (`.venv`), FFmpeg (`acrossfade`, `aformat`, `atrim`, `afade`), pytest.

## Global Constraints

- `--music` accepts a directory (audio files `.mp3/.m4a/.aac/.wav`, case-insensitive, non-recursive, sorted by filename) or a single file.
- Bed = tracks joined with `acrossfade=d=music_crossfade_seconds`; the ordered list repeats to fill `total_duration`; `afade` out at `total_duration - fade_out_seconds`; result is exactly `total_duration` long.
- The bed is mixed under the whole final video (unchanged `_apply_music` mix step). On any music failure → warn + render without music.
- Existing single-`--video` and multi-file behaviour is untouched except the music bed.
- Existing suite must stay green (removed loop tests are replaced). Use `.venv/bin/python`. Real-ffmpeg tests use `@pytest.mark.skipif(shutil.which("ffmpeg") is None, ...)` (test_sync.py already imports `shutil as _sh6` for this).
- `git add` only the files each task changes; never `git add -A` (stray `*.mp3`/scratchpad exist). Deletions use `git rm`.
- Commit messages end with a blank line then: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`

---

## Existing interfaces

- `highlight_detector.get_video_duration(path) -> float` (works on audio files).
- `config.Config`: has `music_crossfade_seconds` (keep), `music_volume`, `original_audio_volume`, `fade_out_seconds` (keep), and `music_min_loop_seconds`, `music_outro_seconds` (REMOVE in Task 2).
- `intro_generator._apply_music(combined_path, output, args, cfg)` currently imports `music_bed`/`music_loop`, resolves a loop, builds a bed, and mixes. `build_final_video` calls it when `args.music`.
- `main.build_parser()` has `--music`, `--music-loop-start`, `--music-loop-end` (REMOVE the two loop flags in Task 2).
- To remove in Task 2: `music_loop.py`, `music_bed.py`, and their tests in `test_sync.py` (`test_decode_pcm_length`, `test_detect_loop_on_periodic_tone`, `test_detect_loop_short_track_fallback`, `test_build_music_bed_long_exact_duration`, `test_build_music_bed_short_linear`).

---

## Task 1: music_playlist.py — gather tracks + build the playlist bed

**Files:** Create `music_playlist.py`; Test `test_sync.py`.

**Interfaces produced:**
- `gather_tracks(music_path) -> list[str]`.
- `build_music_bed(music_path, total_duration, out_path, cfg) -> str`.

- [ ] **Step 1: Failing tests** — append to `test_sync.py`:

```python
def test_gather_tracks_dir_sorted_audio_only(tmp_path):
    import music_playlist
    for name in ["b.mp3", "a.mp3", "d.wav", "c.txt", "notes.md"]:
        (tmp_path / name).write_bytes(b"x")
    tracks = music_playlist.gather_tracks(str(tmp_path))
    assert [__import__("os").path.basename(t) for t in tracks] == ["a.mp3", "b.mp3", "d.wav"]


def test_gather_tracks_single_file(tmp_path):
    import music_playlist
    f = tmp_path / "song.mp3"; f.write_bytes(b"x")
    assert music_playlist.gather_tracks(str(f)) == [str(f)]


def test_gather_tracks_empty_dir_raises(tmp_path):
    import music_playlist, pytest as _pt
    (tmp_path / "readme.txt").write_bytes(b"x")
    with _pt.raises(RuntimeError):
        music_playlist.gather_tracks(str(tmp_path))


@pytest.mark.skipif(_sh6.which("ffmpeg") is None or _sh6.which("ffprobe") is None,
                    reason="ffmpeg not installed")
def test_playlist_bed_repeats_to_fill(tmp_path):
    import subprocess, music_playlist
    from config import Config
    d = tmp_path / "music"; d.mkdir()
    for i, freq in enumerate([220, 330]):
        subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", f"sine=frequency={freq}:d=3",
                        str(d / f"{i}_t.mp3")], check=True, capture_output=True)
    out = tmp_path / "bed.m4a"
    cfg = Config(); cfg.music_crossfade_seconds = 1.0
    # two 3s tracks (crossfaded ~5s) must repeat to fill 12s
    music_playlist.build_music_bed(str(d), 12.0, str(out), cfg)
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout)
    assert abs(dur - 12.0) < 0.3


@pytest.mark.skipif(_sh6.which("ffmpeg") is None or _sh6.which("ffprobe") is None,
                    reason="ffmpeg not installed")
def test_playlist_bed_single_track_trimmed(tmp_path):
    import subprocess, music_playlist
    from config import Config
    f = tmp_path / "long.mp3"
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=220:d=40",
                    str(f)], check=True, capture_output=True)
    out = tmp_path / "bed.m4a"
    music_playlist.build_music_bed(str(f), 8.0, str(out), Config())
    dur = float(subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                                "-of", "csv=p=0", str(out)], capture_output=True, text=True).stdout)
    assert abs(dur - 8.0) < 0.3
```

- [ ] **Step 2: Run — FAIL** (`ModuleNotFoundError: music_playlist`):
`.venv/bin/python -m pytest test_sync.py -k "gather_tracks or playlist_bed" -v`

- [ ] **Step 3: Implement `music_playlist.py`:**

```python
from __future__ import annotations

import os
import subprocess

from highlight_detector import get_video_duration

_AUDIO_EXTS = (".mp3", ".m4a", ".aac", ".wav")
_MAX_INPUTS = 500


def gather_tracks(music_path: str) -> list:
    """Audio files for the playlist: a directory's audio files (non-recursive, sorted by
    filename) or a single file. Raises RuntimeError if nothing usable is found."""
    if os.path.isdir(music_path):
        tracks = sorted(
            os.path.join(music_path, f) for f in os.listdir(music_path)
            if f.lower().endswith(_AUDIO_EXTS))
        if not tracks:
            raise RuntimeError(
                f"Geen audiobestanden ({', '.join(_AUDIO_EXTS)}) in {music_path}")
        return tracks
    if os.path.isfile(music_path):
        return [music_path]
    raise RuntimeError(f"Muziekpad bestaat niet: {music_path}")


def build_music_bed(music_path: str, total_duration: float, out_path: str, cfg) -> str:
    """Assemble a bed of exactly total_duration: tracks back-to-back with crossfades,
    the ordered list repeated to fill, faded out at the end."""
    tracks = gather_tracks(music_path)
    durs = [get_video_duration(t) for t in tracks]
    xf = cfg.music_crossfade_seconds
    if durs:
        xf = max(0.1, min(xf, min(durs) / 2.0))   # keep acrossfade valid on short tracks
    fade = cfg.fade_out_seconds
    fstart = max(0.0, total_duration - fade)

    # Repeat the ordered list until the crossfaded length covers total_duration.
    # Crossfaded length of m tracks = sum(dur) - (m-1)*xf.
    seq = []
    length = 0.0
    idx = 0
    while True:
        k = idx % len(tracks)
        seq.append(tracks[k])
        length = sum(durs[i % len(tracks)] for i in range(len(seq))) - (len(seq) - 1) * xf
        if length >= total_duration or len(seq) >= _MAX_INPUTS:
            break
        idx += 1
    if length < total_duration and len(seq) >= _MAX_INPUTS:
        print(f"[warn] muziek korter dan de video (max {_MAX_INPUTS} tracks); "
              "de fade-out dekt de staart.")

    inputs = []
    for t in seq:
        inputs += ["-i", t]

    if len(seq) == 1:
        filt = (f"[0:a]aformat=sample_rates=44100:channel_layouts=stereo,"
                f"atrim=0:{total_duration},afade=t=out:st={fstart}:d={fade}[out]")
    else:
        parts = [f"[{i}:a]aformat=sample_rates=44100:channel_layouts=stereo[a{i}]"
                 for i in range(len(seq))]
        chain = "[a0]"
        for i in range(1, len(seq)):
            nxt = "[mix]" if i == len(seq) - 1 else f"[m{i}]"
            parts.append(f"{chain}[a{i}]acrossfade=d={xf}{nxt}")
            chain = nxt
        parts.append(f"[mix]atrim=0:{total_duration},"
                     f"afade=t=out:st={fstart}:d={fade}[out]")
        filt = ";".join(parts)

    subprocess.run(["ffmpeg", "-y", "-v", "error", *inputs,
                    "-filter_complex", filt, "-map", "[out]", out_path],
                   check=True, capture_output=True)
    return out_path
```

- [ ] **Step 4: Run — PASS** (`-k "gather_tracks or playlist_bed"`). **Step 5: Full suite** (`.venv/bin/python -m pytest test_sync.py -q`; +5 tests). **Step 6: Commit** `feat: music_playlist — folder of tracks played back-to-back with crossfades`.

---

## Task 2: rewire to the playlist; remove loop detection

**Files:** Modify `intro_generator.py`, `config.py`, `main.py`, `README.md`, `test_sync.py`; Delete `music_loop.py`, `music_bed.py`.

- [ ] **Step 1: Rewire `_apply_music`** in `intro_generator.py` — replace its body (the loop-resolution + `music_bed`/`music_loop` imports) with the playlist call, keeping the same mix and try/except fallback:

```python
def _apply_music(combined_path: str, output: str, args, cfg: Config) -> str:
    """Build the playlist music bed at the video's length and mix it under the audio.
    On any failure, fall back to the un-scored video."""
    from highlight_detector import get_video_duration
    from video_editor import _run_ffmpeg_progress
    try:
        import music_playlist
        total = get_video_duration(combined_path)
        workdir = tempfile.mkdtemp(prefix="rhe_music_")
        try:
            bed = music_playlist.build_music_bed(
                args.music, total, os.path.join(workdir, "bed.m4a"), cfg)
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

(If any leftover references to `args.music_loop_start`/`music_loop_end`, `music_loop`, or `music_bed` remain elsewhere in `intro_generator.py`, remove them.)

- [ ] **Step 2: Remove config fields** — in `config.py`, delete the `music_min_loop_seconds` and `music_outro_seconds` lines from `Config` (keep `music_crossfade_seconds`, `music_volume`, `original_audio_volume`, `fade_out_seconds`).

- [ ] **Step 3: Remove CLI loop flags + update `--music` help** in `main.build_parser()`:
  - Delete the `--music-loop-start` and `--music-loop-end` `add_argument` lines.
  - Change `--music` help to: `"Path to an audio file or a folder of audio files (.mp3/.m4a/.aac/.wav) to play under the video."`

- [ ] **Step 4: Delete the old modules and their tests.**
  - `git rm music_loop.py music_bed.py`
  - In `test_sync.py`, delete these obsolete test functions entirely: `test_decode_pcm_length`, `test_detect_loop_on_periodic_tone`, `test_detect_loop_short_track_fallback`, `test_build_music_bed_long_exact_duration`, `test_build_music_bed_short_linear`. (Leave the shared `import shutil as _sh6` helper — other tests use it.)

- [ ] **Step 5: Update the final-video music test** — in `test_sync.py`, find `test_build_final_video_with_music_has_audio`. Change its music input to a FOLDER and drop the removed `music_loop_start`/`music_loop_end` attrs from the fake args:

```python
    # build a small music folder instead of a single loop track
    mdir = tmp_path / "music"; mdir.mkdir()
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=220:d=10",
                    str(mdir / "01.mp3")], check=True, capture_output=True)
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i", "sine=frequency=300:d=10",
                    str(mdir / "02.mp3")], check=True, capture_output=True)
    ...
    class A:
        video = str(reel); strava = False; garmin = False; music = str(mdir)
    ...
```

(Keep the rest of that test as-is: build the reel + GpxData, call `build_final_video`, assert the output has an audio stream. Remove any `music_loop_start`/`music_loop_end` lines.)

- [ ] **Step 6: Verify** — run `.venv/bin/python -c "import main, intro_generator, config; import pytest"` and confirm `music_loop`/`music_bed` are gone (`.venv/bin/python -c "import music_loop"` must fail with ModuleNotFoundError). Run the FULL suite `.venv/bin/python -m pytest test_sync.py -q` — all green (the removed loop tests are gone; the playlist tests from Task 1 and the updated final-video test pass). Also `grep -rn "music_loop\|music_bed\|music_min_loop\|music_outro\|music-loop" main.py intro_generator.py config.py` returns nothing.

- [ ] **Step 7: README** — replace the music note with the playlist behaviour:

```markdown
Pass `--music ./music/` (a folder) or `--music track.mp3` (a single file). The tool plays
the audio files back-to-back with short crossfades — folder contents sorted by filename
(`.mp3/.m4a/.aac/.wav`) — repeating the list to fill the whole video, and fades out at the
end. Mixed under the entire video (intro included), with the original audio quiet beneath.
```

- [ ] **Step 8: Commit** `feat: play a folder of music tracks; remove loop detection` (stage `intro_generator.py config.py main.py README.md test_sync.py` and the `git rm` of the two modules).

---

## Self-Review

**Spec coverage:**
- `gather_tracks` (dir sorted audio-only / single file / empty raises) → Task 1. ✓
- `build_music_bed` (crossfade chain, repeat-to-fill, single-track trim, fade, exact duration) → Task 1. ✓
- `_apply_music` rewired to playlist, mix + fallback unchanged → Task 2. ✓
- Config fields removed; CLI loop flags removed; `--music` help updated → Task 2. ✓
- `music_loop.py`/`music_bed.py` + their tests deleted; final-video test uses a folder → Task 2. ✓
- README updated → Task 2. ✓

**Placeholder scan:** none.

**Type consistency:** `gather_tracks(music_path) -> list[str]` and `build_music_bed(music_path, total_duration, out_path, cfg) -> str` (Task 1) match the call in `_apply_music` (Task 2). `music_crossfade_seconds`/`fade_out_seconds`/`music_volume`/`original_audio_volume` are the only config fields read. No references to the removed modules/fields remain after Task 2 (Step 6 grep gate).
