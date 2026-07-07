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
    outro_len = min(getattr(cfg, 'music_outro_seconds', 3.0), track_dur / 2.0)
    xf = getattr(cfg, 'music_crossfade_seconds', 1.0)

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
