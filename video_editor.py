from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

from config import Config
from highlight_detector import Segment
from segment_detector import format_segment_stats


def _escape_drawtext(text: str) -> str:
    """Escape characters special to FFmpeg drawtext text values."""
    text = text.replace("\\", "\\\\")
    text = text.replace(":", "\\:")
    text = text.replace("'", "\\'")
    text = text.replace("%", "\\%")
    return text


def _lower_third_filter(name: str, stats: str, cfg: Config) -> str:
    """Build a filtergraph: a semi-transparent lower band + name and stats lines."""
    font = cfg.overlay_font_path
    band_h = cfg.overlay_name_fontsize + cfg.overlay_stats_fontsize + 60
    box = (f"drawbox=x=0:y=ih-{band_h}:w=iw:h={band_h}:"
           f"color=black@{cfg.overlay_band_opacity}:t=fill")
    name_txt = (f"drawtext=fontfile='{font}':text='{_escape_drawtext(name)}':"
                f"x=40:y=h-{band_h}+20:fontsize={cfg.overlay_name_fontsize}:"
                f"fontcolor=white")
    stats_txt = (f"drawtext=fontfile='{font}':text='{_escape_drawtext(stats)}':"
                 f"x=40:y=h-{cfg.overlay_stats_fontsize}-20:"
                 f"fontsize={cfg.overlay_stats_fontsize}:fontcolor=white")
    return f"{box},{name_txt},{stats_txt}"


def check_ffmpeg() -> None:
    """Ensure ffmpeg and ffprobe are available; raise a clear error otherwise."""
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise RuntimeError(
                f"{tool} not found. Install FFmpeg via Homebrew:\n"
                "    brew install ffmpeg"
            )


def _amix_filter(cfg: Config, has_music: bool, reel_duration: float) -> str:
    """Build the FFmpeg filtergraph for mixing original audio under music."""
    if not has_music:
        return ""
    fade_start = max(0.0, reel_duration - cfg.fade_out_seconds)
    return (
        f"[0:a]volume={cfg.original_audio_volume}[a0];"
        f"[1:a]volume={cfg.music_volume},"
        f"afade=t=out:st={fade_start}:d={cfg.fade_out_seconds}[a1];"
        f"[a0][a1]amix=inputs=2:duration=first:dropout_transition=0[aout]"
    )


def _cut_segment(video_path: str, seg: Segment, out_path: str, cfg: Config) -> None:
    dur = seg.end - seg.start
    if cfg.cut_mode == "copy":
        cmd = ["ffmpeg", "-y", "-ss", str(seg.start), "-i", video_path,
               "-t", str(dur), "-c", "copy", out_path]
    else:
        cmd = ["ffmpeg", "-y", "-ss", str(seg.start), "-i", video_path,
               "-t", str(dur), "-c:v", "libx264", "-preset", "veryfast",
               "-c:a", "aac", out_path]
    subprocess.run(cmd, check=True, capture_output=True)


def _concat_and_music(part_paths, music_path, reel_duration, workdir, cfg: Config) -> str:
    """Concat pre-cut parts, then optionally mix music with fade-out. Returns path."""
    concat_list = os.path.join(workdir, "concat.txt")
    with open(concat_list, "w") as f:
        for p in part_paths:
            f.write(f"file '{p}'\n")
    reel = os.path.join(workdir, "reel.mp4")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_list,
         "-c", "copy", reel],
        check=True, capture_output=True,
    )
    if not music_path:
        return reel
    mixed = os.path.join(workdir, "reel_music.mp4")
    filtergraph = _amix_filter(cfg, has_music=True, reel_duration=reel_duration)
    subprocess.run(
        ["ffmpeg", "-y", "-i", reel, "-i", music_path,
         "-filter_complex", filtergraph,
         "-map", "0:v", "-map", "[aout]",
         "-c:v", "copy", "-c:a", "aac", "-shortest", mixed],
        check=True, capture_output=True,
    )
    return mixed


def build_highlight_reel(video_path, segments, music_path, cfg: Config) -> str:
    """Cut selected segments, concat them, and mix music. Returns output path."""
    check_ffmpeg()
    workdir = tempfile.mkdtemp(prefix="rhe_")
    part_paths = []
    for i, seg in enumerate(segments):
        part = os.path.join(workdir, f"part_{i:03d}.mp4")
        _cut_segment(video_path, seg, part, cfg)
        part_paths.append(part)

    reel_duration = sum(seg.end - seg.start for seg in segments)
    return _concat_and_music(part_paths, music_path, reel_duration, workdir, cfg)


def build_segment_reel(video_path, clips, music_path, cfg: Config) -> str:
    """Cut each segment clip with a lower-third overlay, concat, and mix music."""
    check_ffmpeg()
    workdir = tempfile.mkdtemp(prefix="rhe_seg_")
    part_paths = []
    for i, clip in enumerate(clips):
        part = os.path.join(workdir, f"seg_{i:03d}.mp4")
        stats = format_segment_stats(clip)
        vf = _lower_third_filter(clip.name, stats, cfg)
        dur = clip.end - clip.start
        subprocess.run(
            ["ffmpeg", "-y", "-ss", str(clip.start), "-i", video_path,
             "-t", str(dur), "-vf", vf,
             "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", part],
            check=True, capture_output=True,
        )
        part_paths.append(part)
    reel_duration = sum(c.end - c.start for c in clips)
    return _concat_and_music(part_paths, music_path, reel_duration, workdir, cfg)
