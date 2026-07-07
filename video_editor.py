from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass

from config import Config
from highlight_detector import Segment
from segment_detector import format_segment_stats
from telemetry import sample_telemetry
import hud_renderer


def _log(msg, end="\n"):
    """Emit a progress line to stderr (keeps stdout/pipes clean)."""
    print(msg, end=end, file=sys.stderr, flush=True)


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


def _escape_drawtext(text: str) -> str:
    """Escape characters special to FFmpeg drawtext text values."""
    text = text.replace("\\", "\\\\")
    text = text.replace(":", "\\:")
    text = text.replace("%", "\\%")
    # Close the single-quoted string, emit an escaped quote, then reopen the
    # quoted string. A backslash-escaped quote (\') is NOT valid inside a
    # single-quoted ffmpeg filter argument, so this must be done last (after
    # backslash escaping above) to avoid doubling the backslash it introduces.
    text = text.replace("'", "'\\''")
    return text


def _lower_third_filter(name: str, stats: str, cfg: Config) -> str:
    """Build a filtergraph: a semi-transparent lower band + name and stats lines."""
    font = cfg.overlay_font_path
    band_h = cfg.overlay_name_fontsize + cfg.overlay_stats_fontsize + 60
    # drawbox uses ih (input height); drawtext below uses h instead because
    # drawtext's expression evaluator does not define ih (only h).
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
    _log("Segmenten samenvoegen…")
    subprocess.run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_list,
         "-c", "copy", reel],
        check=True, capture_output=True,
    )
    if not music_path:
        return reel
    mixed = os.path.join(workdir, "reel_music.mp4")
    _log("Muziek mixen…")
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
    n = len(segments)
    for i, seg in enumerate(segments):
        _log(f"[{i + 1}/{n}] segment {seg.start:.0f}-{seg.end:.0f}s knippen…")
        part = os.path.join(workdir, f"part_{i:03d}.mp4")
        _cut_segment(video_path, seg, part, cfg)
        part_paths.append(part)

    reel_duration = sum(seg.end - seg.start for seg in segments)
    return _concat_and_music(part_paths, music_path, reel_duration, workdir, cfg)


def _render_hud_pngs(video_path, clip, source, gpx, offset_seconds, workdir, cfg):
    """Render the HUD PNG sequence for one clip; return the printf pattern path."""
    import os
    import subprocess, json
    probe = subprocess.run(
        ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_streams", video_path],
        capture_output=True, text=True, check=True)
    vs = next(s for s in json.loads(probe.stdout)["streams"] if s["codec_type"] == "video")
    W, H = int(vs["width"]), int(vs["height"])

    seg_start_activity = clip.start + offset_seconds
    seg_coords = _segment_coords(gpx, clip, offset_seconds)
    date_str = gpx.start_time.strftime("%d-%m-%Y")

    hud_dir = os.path.join(workdir, "hud")
    os.makedirs(hud_dir, exist_ok=True)
    dur = clip.end - clip.start
    n_frames = max(1, int(round(dur * cfg.hud_fps)))
    for i in range(n_frames):
        t_video = clip.start + (i / cfg.hud_fps)
        activity_t = t_video + offset_seconds
        sample = sample_telemetry(source, activity_t, seg_start_activity)
        frame = hud_renderer.render_hud_frame(
            sample, clip.name, seg_coords, (W, H), cfg, date_str)
        frame.save(os.path.join(hud_dir, f"hud_{i:06d}.png"))
        if i % 5 == 0 or i == n_frames - 1:
            _log(f"\r        frames {i + 1}/{n_frames} ({(i + 1) * 100 // n_frames}%)", end="")
    _log("")  # end the \r progress line
    return os.path.join(hud_dir, "hud_%06d.png")


def _segment_coords(gpx, clip, offset_seconds):
    """The GPX coords covering the clip's activity-time window (for the minimap)."""
    a0 = int(max(0, clip.start + offset_seconds))
    a1 = int(min(len(gpx.coords) - 1, clip.end + offset_seconds))
    pts = gpx.coords[a0:a1 + 1]
    return pts if len(pts) >= 2 else gpx.coords[:2] or [(0.0, 0.0), (0.0, 0.0)]


def build_segment_reel(video_path, clips, music_path, cfg: Config,
                       gpx=None, offset_seconds=0.0, telemetry_source=None) -> str:
    """Cut each segment clip with an overlay (HUD if gpx given, else lower-third),
    concat, and mix music."""
    check_ffmpeg()
    workdir = tempfile.mkdtemp(prefix="rhe_seg_")
    part_paths = []
    use_hud = cfg.hud_enabled and gpx is not None
    source = telemetry_source if telemetry_source is not None else gpx
    n = len(clips)
    for i, clip in enumerate(clips):
        part = os.path.join(workdir, f"seg_{i:03d}.mp4")
        dur = clip.end - clip.start
        if use_hud:
            _log(f"[{i + 1}/{n}] {clip.name} ({dur:.1f}s) — HUD-frames renderen…")
            try:
                pattern = _render_hud_pngs(video_path, clip, source, gpx, offset_seconds, workdir, cfg)
                overlay_cmd = [
                    "ffmpeg", "-y", "-ss", str(clip.start), "-t", str(dur), "-i", video_path,
                    "-framerate", str(cfg.hud_fps), "-i", pattern,
                    "-filter_complex", "[0:v][1:v]overlay=0:0:shortest=1[v]",
                    "-map", "[v]", "-map", "0:a?",
                    "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", part]
                _run_ffmpeg_progress(overlay_cmd, dur, f"[{i + 1}/{n}] {clip.name} — overlay encoderen")
                part_paths.append(part)
                continue
            except Exception as e:
                print(f"[warn] HUD render failed ({e}); falling back to lower-third.")
        # fallback / hud disabled: lower-third band
        _log(f"[{i + 1}/{n}] {clip.name} — lower-third…")
        stats = format_segment_stats(clip)
        vf = _lower_third_filter(clip.name, stats, cfg)
        subprocess.run(
            ["ffmpeg", "-y", "-ss", str(clip.start), "-i", video_path,
             "-t", str(dur), "-vf", vf,
             "-c:v", "libx264", "-preset", "veryfast", "-c:a", "aac", part],
            check=True, capture_output=True,
        )
        part_paths.append(part)
    reel_duration = sum(c.end - c.start for c in clips)
    return _concat_and_music(part_paths, music_path, reel_duration, workdir, cfg)


@dataclass
class _PartClip:
    """Shim so a RenderPart can be handed to _segment_coords/format_segment_stats,
    which expect a clip-like object with .start/.end/.name/.stats."""
    start: float
    end: float
    name: "str | None"
    stats: dict


def _render_hud_pngs_for_part(part, source, gpx, target_size, workdir, cfg, part_index=0):
    """HUD PNG sequence for a RenderPart, sized to target_size, telemetry at
    activity_t = local_t + part.base_offset. Returns the printf pattern path."""
    W, H = target_size
    clip = _PartClip(part.local_start, part.local_end, part.name, part.stats or {})
    seg_start_activity = part.local_start + part.base_offset
    seg_coords = _segment_coords(gpx, clip, part.base_offset)
    date_str = gpx.start_time.strftime("%d-%m-%Y")
    hud_dir = os.path.join(workdir, f"hud_{part_index:03d}")
    os.makedirs(hud_dir, exist_ok=True)
    dur = clip.end - clip.start
    n_frames = max(1, int(round(dur * cfg.hud_fps)))
    for i in range(n_frames):
        activity_t = clip.start + (i / cfg.hud_fps) + part.base_offset
        sample = sample_telemetry(source, activity_t, seg_start_activity)
        frame = hud_renderer.render_hud_frame(sample, clip.name, seg_coords, (W, H), cfg, date_str)
        frame.save(os.path.join(hud_dir, f"hud_{i:06d}.png"))
    return os.path.join(hud_dir, "hud_%06d.png")


def build_reel_from_parts(parts, cfg: Config, target_size, gpx=None,
                          telemetry_source=None) -> str:
    """Cut each RenderPart from its own source, scale to target_size so parts from
    different files concatenate, overlay HUD/lower-third when the part is named, and
    concat in order. Music is applied later (final stage), so none here."""
    check_ffmpeg()
    W, H = target_size
    workdir = tempfile.mkdtemp(prefix="rhe_parts_")
    scale = f"scale={W}:{H},setsar=1"
    source = telemetry_source if telemetry_source is not None else gpx
    use_hud = cfg.hud_enabled and gpx is not None
    part_paths = []
    n = len(parts)
    for i, part in enumerate(parts):
        out = os.path.join(workdir, f"part_{i:03d}.mp4")
        dur = part.local_end - part.local_start
        if part.name is not None and use_hud:
            _log(f"[{i+1}/{n}] {part.name} ({dur:.1f}s) — HUD…")
            try:
                pattern = _render_hud_pngs_for_part(part, source, gpx, (W, H), workdir, cfg, part_index=i)
                cmd = ["ffmpeg", "-y", "-ss", str(part.local_start), "-t", str(dur),
                       "-i", part.source_path, "-framerate", str(cfg.hud_fps), "-i", pattern,
                       "-filter_complex", f"[0:v]{scale}[v0];[v0][1:v]overlay=0:0:shortest=1[v]",
                       "-map", "[v]", "-map", "0:a?", "-c:v", "libx264", "-preset", "veryfast",
                       "-c:a", "aac", out]
                _run_ffmpeg_progress(cmd, dur, f"[{i+1}/{n}] {part.name} — overlay")
                part_paths.append(out)
                continue
            except Exception as e:
                print(f"[warn] HUD render failed ({e}); falling back to lower-third.")
        if part.name is not None:
            vf = f"{scale}," + _lower_third_filter(part.name, format_segment_stats(
                _PartClip(part.local_start, part.local_end, part.name, part.stats or {})), cfg)
        else:
            vf = scale
        subprocess.run(["ffmpeg", "-y", "-ss", str(part.local_start), "-i", part.source_path,
                        "-t", str(dur), "-vf", vf, "-c:v", "libx264", "-preset", "veryfast",
                        "-c:a", "aac", out], check=True, capture_output=True)
        part_paths.append(out)

    reel_duration = sum(p.local_end - p.local_start for p in parts)
    return _concat_and_music(part_paths, None, reel_duration, workdir, cfg)
