# Adaptive music bed (structured loop + natural outro) — Design

**Date:** 2026-07-07
**Status:** Approved for planning (assembly validated by an audio demo on the real track)
**Context:** Feature #2 of three (auto-GPX ✓ → **auto-music** → multi-clip). The user
supplies a music track; the tool should build a bed that fits the exact final-video
length by playing the track from the start, looping a seamless region to fill the
middle, and ending with the track's natural outro — so the song resolves like the
original rather than fading mid-loop. The loopable region is auto-detected (with a
manual override). Validated with a rendered demo on the real `bg_music.mp3`.

## Decisions (from brainstorming + demo)

- Bed spans the **whole final video** (intro + reel); music moves from the reel stage
  to the **final assembly** so the intro also gets music.
- Bed structure (when the video is long enough to need filling):
  `head[0:loop_end]` + `loop[loop_start:loop_end]` repeated to fill the middle +
  **natural outro** = the track's last `music_outro_seconds`, joined with a short
  `music_crossfade_seconds` crossfade, then a final fade at the exact end.
- Short video (target < room for head+outro) → just `music[0:target]` + fade.
- Loop region auto-detected (early, seamless, ≥ `music_min_loop_seconds`); overridable
  with `--music-loop-start` / `--music-loop-end`.

## Components

- `music_loop.py` (new):
  - `_decode_pcm(path, sr=22050) -> np.ndarray` — decode to mono float32 via
    `ffmpeg -i path -ac 1 -ar {sr} -f f32le -` → `np.frombuffer`.
  - `detect_loop(path, min_loop_seconds, sr=22050, a_max_s=60.0, max_loop_s=25.0)
    -> tuple[float, float]` — find an early, seamless loop `(loop_start, loop_end)`:
    decode; over a 0.5 s grid, search loop-in `a ∈ [5 s, a_max_s]` and loop-out
    `b ∈ [a+min_loop, a+max_loop]`; seam metric = normalised difference of the 0.12 s
    pre-windows at `a` and `b` (so the wrap `b→a` is smooth); pick the min; snap both
    to the nearest zero crossing. Fallback (track too short for a loop) → `(0.0,
    duration)`.
- `music_bed.py` (new):
  - `build_music_bed(music_path, total_duration, loop_start, loop_end, out_path, cfg)
    -> str` — assemble a bed of **exactly** `total_duration` seconds:
    - `track_dur` via ffprobe; `outro_len = min(cfg.music_outro_seconds, track_dur/2)`;
      `xf = cfg.music_crossfade_seconds`.
    - **If** `total_duration < loop_end + outro_len - xf` (too short to fit head+outro)
      → `ffmpeg -i music -t {total_duration} -af afade=t=out:st={total-fade}:d={fade}`.
    - **Else**: `head = music[0:loop_end]`; `fill_len = total_duration - outro_len + xf
      - loop_end`; `loop = music[loop_start:loop_end]` → `-stream_loop -1 ... -t
      {fill_len}` → `fill`; concat `head`+`fill` → `headfill`; `outro = music[track_dur
      - outro_len : track_dur]`; `acrossfade` `headfill`+`outro` (d=xf); `afade` out at
      `total_duration - fade`; `atrim` to `total_duration` for exactness. WAV
      intermediates (lossless); output `out_path`.
- `config.py`: `music_min_loop_seconds: float = 8.0`, `music_outro_seconds: float = 10.0`,
  `music_crossfade_seconds: float = 1.5`. Reuse `music_volume`, `original_audio_volume`,
  `fade_out_seconds`.
- `video_editor.py`: the reel builders **stop mixing music** — `build_highlight_reel`
  and `build_segment_reel` build the reel with its original audio only (no music
  argument used for mixing; `_concat_and_music` becomes a plain concat, or is called
  with `music_path=None`). Music is now applied only in the final stage.
- `intro_generator.build_final_video`: after producing the intro+reel concat, if
  `args.music` is set: resolve the loop region (`--music-loop-*` overrides else
  `music_loop.detect_loop(args.music, cfg.music_min_loop_seconds)`), build the bed at
  the concat's exact duration (`get_video_duration`), and **mix** it under the whole
  video: `[0:a]volume={original_audio_volume}[a0];[1:a]volume={music_volume}[a1];
  [a0][a1]amix=inputs=2:duration=first[aout]`, map `[0:v] + [aout]`. No music → the
  concat is the output (unchanged). The bed carries its own outro/fade, so the mix adds
  no extra fade.
- `main.py`: add `--music-loop-start` / `--music-loop-end` (float, optional); stop
  passing `args.music` into `build_*_reel`; pass music via `build_final_video` (it
  already receives `args`).

## Data flow

```
reel (original audio, NO music)  +  silent intro
  → build_final_video: concat intro+reel  → combined (video + [silent intro | reel audio])
  → if args.music:
        loop_start/end = overrides or music_loop.detect_loop(args.music, min_loop)
        bed = music_bed.build_music_bed(args.music, dur(combined), loop_start, loop_end, ..., cfg)
        mix bed under combined (orig_vol + music_vol) → output
     else: combined → output
```

## Error handling

- No `--music` → no bed, no mixing (intro silent + reel audio), unchanged behaviour.
- `detect_loop` on a very short track → `(0.0, track_dur)`; `build_music_bed` then takes
  the short-video linear branch when appropriate.
- Music mixing / bed build failure → warn and fall back to no music (the video still
  renders); the reel/intro already carry their audio.
- `--music-loop-end` beyond the track / start ≥ end → clamp to the track and fall back
  to `detect_loop` with a warning.

## Testing (pytest)

- `_decode_pcm`: decodes a short ffmpeg-generated sine to a non-empty float32 array of
  the expected length (skipif no ffmpeg).
- `detect_loop`: on a synthetic tone whose waveform repeats with a known period, the
  detected `(start,end)` has `end-start ≥ min_loop` and a low seam; a too-short track →
  `(0.0, duration)`. (Real-ffmpeg, skipif.)
- `build_music_bed` (real-ffmpeg): output duration == `total_duration` for (a) the
  long case (head+loop-fill+outro) and (b) the short case (`total < room` → linear);
  both non-empty.
- `build_final_video` end-to-end with `--music` (real-ffmpeg): output has an audio
  stream and the requested duration; without `--music` it still renders (no regression).
- CLI/wiring: `--music-loop-start/-end` parsed; the reel builders no longer mix music
  (music appears only in the final output).

## Out of scope

- The royalty-free music **library** / auto-selection from a directory (deferred, noted).
- Multiple clips (feature #3, separate spec).
- Beat/tempo-aware looping (the seam-based detector is enough; manual override covers
  the rest).
