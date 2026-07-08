# Music from a folder, played back-to-back — Design

**Date:** 2026-07-08
**Status:** Approved for planning
**Context:** The adaptive loop-detection bed (feature #2: `music_loop.py` +
`music_bed.py`) doesn't loop cleanly enough in practice and is complex. Replace it with a
simple, predictable playlist: `--music` points at a folder of tracks (or a single file),
the tracks play back-to-back with short crossfades, the list repeats to fill the video,
and it fades out at the exact end. This **replaces** the loop approach entirely.

## Decisions (from brainstorming)

- `--music` accepts a **directory** or a **single file**. Directory → all audio files
  (`.mp3`, `.m4a`, `.aac`, `.wav`, case-insensitive) directly in that folder
  (non-recursive), sorted **alphabetically by filename** (rename `01_`, `02_`… to order).
  Single file → a one-track playlist.
- Tracks are joined with a short **crossfade** (`music_crossfade_seconds`, ~1.5 s)
  between each track.
- If the tracks together are **shorter** than the video, the playlist **repeats** from
  the first track (crossfade at the repeat point too) until the video is filled.
- A **fade-out** (`fade_out_seconds`) at the exact video length.
- The bed is mixed under the **whole** final video (intro + reel), original audio quiet
  underneath — the existing feature-#2 mixing wiring is unchanged.
- **Removed:** `music_loop.py`, `music_bed.py`, `--music-loop-start`/`--music-loop-end`,
  config `music_min_loop_seconds`, `music_outro_seconds`. Kept: `music_crossfade_seconds`,
  `music_volume`, `original_audio_volume`, `fade_out_seconds`.

## Components

- `music_playlist.py` (new — replaces `music_loop.py` + `music_bed.py`):
  - `gather_tracks(music_path) -> list[str]`: if `music_path` is a directory, return the
    audio files directly in it (extensions above, case-insensitive), sorted by filename;
    if it is a file, return `[music_path]`. Raise `RuntimeError` with a clear message if
    the directory has no audio files (or the path doesn't exist).
  - `build_music_bed(music_path, total_duration, out_path, cfg) -> str`: assemble a bed
    of **exactly** `total_duration` seconds:
    1. `tracks = gather_tracks(music_path)`; probe each duration with
       `highlight_detector.get_video_duration` (works on audio).
    2. Repeat the track list (in order) until the crossfaded length ≥ `total_duration`.
       Crossfaded length of `m` tracks = `sum(durations) - (m-1)*xf` where
       `xf = cfg.music_crossfade_seconds`. Cap the repeated list at a sane maximum
       (e.g. 500 inputs); if the cap is hit, log that the bed may be short (the fade
       covers the tail).
    3. One FFmpeg `filter_complex`: normalise each input
       (`[i:a]aformat=sample_rates=44100:channel_layouts=stereo`), then chain
       `acrossfade=d=xf` across the whole (possibly repeated) list, `atrim=0:total_duration`,
       `afade=t=out:st=(total_duration-fade):d=fade`. Single track that already exceeds
       `total_duration` → skip acrossfade, just `atrim`+`afade`. WAV/temp handling as
       needed; write `out_path`.
- `intro_generator._apply_music(combined_path, output, args, cfg)`: replace the
  loop-detection body with `bed = music_playlist.build_music_bed(args.music,
  get_video_duration(combined_path), <workdir>/bed.m4a, cfg)`, then the existing mix
  (`[0:a]volume={original_audio_volume}`, `[1:a]volume={music_volume}`, `amix`,
  `-map 0:v -map [aout]`, `-c:v copy`). Keep the try/except fallback: on any failure,
  warn and copy the combined video to output (renders without music). Drop the
  `--music-loop-*` override/validation code.
- `config.py`: remove `music_min_loop_seconds`, `music_outro_seconds`. Keep the rest.
- `main.py`: remove `--music-loop-start` / `--music-loop-end`; update `--music` help to
  "path to an audio file or a folder of audio files".
- Delete `music_loop.py` and `music_bed.py`.

## Data flow

```
--music ./muziek/  (or track.mp3)
  → gather_tracks → [t1, t2, …] (sorted; single file → [file])
  → build_music_bed(total = final-video duration):
       repeat list until crossfaded length ≥ total
       → aformat per input → acrossfade chain → atrim to total → afade out
  → _apply_music mixes the bed under the whole video (orig quiet + music)
```

## Error handling

- `--music` is a folder with no audio files, or a non-existent path → `RuntimeError`
  from `gather_tracks`, caught by `_apply_music` → warn + render without music.
- Any bed-build / mix failure → warn and fall back to the un-scored video (unchanged).
- No `--music` → no bed, no mixing (unchanged).

## Testing (pytest)

- `gather_tracks`: a temp dir with `b.mp3`, `a.mp3`, `c.txt`, `d.wav` → returns
  `[a.mp3, b.mp3, d.wav]` (audio only, sorted, no `.txt`); a single-file path →
  `[path]`; an empty dir / no-audio dir → raises `RuntimeError`.
- `build_music_bed` (real-ffmpeg, skipif): 
  - two short generated tracks whose sum < `total_duration` → output duration ==
    `total_duration` (list repeated to fill), non-empty, has audio;
  - one track longer than `total_duration` → output trimmed to `total_duration`;
  - both fade out at the end (duration is exact within ~0.3 s).
- `_apply_music` / final video with `--music <dir>` (real-ffmpeg): output has an audio
  stream and the expected duration (update the existing music test to pass a folder and
  drop the removed `music_loop_start/end` args).
- Remove the obsolete tests: `_decode_pcm`, `detect_loop` (all), old `build_music_bed`
  (loop signature), and the `--music-loop-*` override-validation test.

## Out of scope

- Recursive folder scanning, playlists files (`.m3u`), shuffle/random order.
- Beat-matching or tempo-aware crossfades.
- Per-track volume normalisation beyond format normalisation for crossfade.
- Keeping the old loop-detection as an option (it is removed).
