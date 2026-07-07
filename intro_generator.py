from __future__ import annotations

import os
import shutil
import subprocess
import tempfile

from config import Config
from highlight_detector import GpxData


def _even(x) -> int:
    """Round to an even integer (libx264 requires even dimensions)."""
    x = int(round(x))
    return x - (x % 2)


def _target_dims(source_w, source_h, output_height):
    """Compute even (width, height) for the output, preserving the source aspect."""
    h = _even(output_height)
    w = _even(source_w * h / source_h)
    return w, h


def format_moving_time(seconds: float) -> str:
    total_min = int(seconds // 60)
    h, m = divmod(total_min, 60)
    if h > 0:
        return f"{h}h {m}m"
    return f"{m}m"


def _geocoder():
    from geopy.geocoders import Nominatim
    return Nominatim(user_agent="ride-highlight-editor")


def reverse_geocode(lat: float, lon: float) -> str:
    """Human-readable place for a coordinate; falls back to raw lat/lon on failure."""
    try:
        loc = _geocoder().reverse((lat, lon), language="en", timeout=10)
        if loc and loc.address:
            addr = loc.raw.get("address", {})
            town = (addr.get("city") or addr.get("town") or addr.get("village")
                    or addr.get("municipality"))
            region = addr.get("state") or addr.get("country")
            if town and region:
                return f"{town}, {region}"
            return loc.address.split(",")[0]
    except Exception:
        pass
    return f"{lat:.4f}, {lon:.4f}"


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


def _gather_extra_stats(gpx: GpxData, args) -> dict:
    stats: dict = {}
    if getattr(args, "strava", False):
        try:
            from strava_client import get_activity_stats
            stats.update(get_activity_stats(getattr(args, "strava_activity_id", None)))
        except Exception as e:
            print(f"[warn] Strava stats skipped: {e}")
    if getattr(args, "garmin", False):
        try:
            from garmin_client import get_activity_stats as garmin_stats
            stats.update(garmin_stats(gpx.start_time))
        except Exception as e:
            print(f"[warn] Garmin stats skipped: {e}")
    return stats


def _concat_intro_and_reel(intro_path: str, reel_path: str, intro_duration: float,
                           output: str, width: int, height: int) -> str:
    """Concat a silent, video-only intro with the reel at width×height, giving the
    intro a real silent audio stream via an anullsrc input so concat's pads balance."""
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


def build_final_video(reel_path: str, gpx: GpxData, cfg: Config, output: str, args) -> str:
    """Prepend the intro to the reel and re-encode at the configured resolution."""
    from highlight_detector import get_video_resolution
    sw, sh = get_video_resolution(args.video)
    W, H = _target_dims(sw, sh, cfg.output_height)
    workdir = tempfile.mkdtemp(prefix="rhe_final_")
    try:
        intro = os.path.join(workdir, "intro.mp4")
        extra = _gather_extra_stats(gpx, args)
        build_intro_clip(gpx, cfg, intro, extra, size=(W, H))
        return _concat_intro_and_reel(intro, reel_path, cfg.intro_duration, output, W, H)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
