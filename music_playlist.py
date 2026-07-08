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
