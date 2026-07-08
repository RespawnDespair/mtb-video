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


def build_intro_clip(gpx: GpxData, cfg: Config, out_path: str, extra_stats: dict | None = None,
                     size=(1920, 1080), video_path=None, offset_seconds=0.0, gpx_path=None,
                     sources=None) -> str:
    """Render the animated intro: curviest bg clip (dimmed) + route-draw overlay."""
    import intro_renderer
    from intro_select import heading_change_per_sec, curviest_window
    from highlight_detector import get_video_duration
    from video_editor import _run_ffmpeg_progress

    W, H = size
    workdir = tempfile.mkdtemp(prefix="rhe_intro_")
    try:
        # 1. overlay PNG sequence
        title = intro_renderer.intro_title(gpx, gpx_path)
        date_str = intro_renderer.intro_date(gpx)
        stats = intro_renderer.intro_stats(gpx, extra_stats)
        frames_dir = os.path.join(workdir, "frames")
        os.makedirs(frames_dir)
        nframes = max(1, int(round(cfg.intro_duration * cfg.intro_fps)))
        for i in range(nframes):
            tn = i / (nframes - 1) if nframes > 1 else 1.0
            frame = intro_renderer.render_intro_frame(tn, gpx.coords, title, date_str, stats, (W, H), cfg)
            frame.save(os.path.join(frames_dir, f"f_{i:05d}.png"))
        pattern = os.path.join(frames_dir, "f_%05d.png")

        # 2. background: curviest clip across sources (multi) or the single video, else solid dark
        bg_clip = None
        turn = heading_change_per_sec(gpx.coords, gpx.speeds_kmh)
        if sources:
            from intro_select import curviest_window_across
            bg_source, vt = curviest_window_across(sources, turn, cfg.intro_duration)
        elif video_path and get_video_duration(video_path) >= cfg.intro_duration:
            vt = curviest_window(turn, offset_seconds, get_video_duration(video_path),
                                 cfg.intro_duration)
            bg_source = video_path
        else:
            bg_source = None
        if bg_source:
            try:
                bg_clip = os.path.join(workdir, "bg.mp4")
                subprocess.run(
                    ["ffmpeg", "-y", "-ss", str(vt), "-t", str(cfg.intro_duration),
                     "-i", bg_source, "-vf", f"scale={W}:{H}", "-an", bg_clip],
                    check=True, capture_output=True)
            except Exception as e:
                print(f"[warn] intro background clip unavailable ({e}); using solid background.")
                bg_clip = None

        # 3. composite the overlay onto the background
        if bg_clip:
            base = ["-i", bg_clip]
        else:
            base = ["-f", "lavfi", "-i",
                    f"color=c=0x0f1014:s={W}x{H}:r={cfg.intro_fps}:d={cfg.intro_duration}"]
        cmd = (["ffmpeg", "-y"] + base
               + ["-framerate", str(cfg.intro_fps), "-i", pattern,
                  "-filter_complex", "[0:v][1:v]overlay=0:0:shortest=1[v]",
                  "-map", "[v]", "-t", str(cfg.intro_duration),
                  "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", out_path])
        _run_ffmpeg_progress(cmd, cfg.intro_duration, "Intro renderen")
        return out_path
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


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


def build_final_video(reel_path: str, gpx: GpxData, cfg: Config, output: str, args,
                      offset_seconds: float = 0.0, sources=None) -> str:
    """Prepend the intro to the reel, re-encode at the configured resolution, and
    (if --music was given) mix an adaptive music bed under the whole video."""
    from highlight_detector import get_video_resolution
    if sources:
        sw, sh = sources[0].width, sources[0].height
    else:
        sw, sh = get_video_resolution(args.video[0] if isinstance(args.video, list) else args.video)
    W, H = _target_dims(sw, sh, cfg.output_height)
    workdir = tempfile.mkdtemp(prefix="rhe_final_")
    try:
        intro = os.path.join(workdir, "intro.mp4")
        extra = _gather_extra_stats(gpx, args)
        build_intro_clip(gpx, cfg, intro, extra, size=(W, H),
                         video_path=(None if sources else
                                     (args.video[0] if isinstance(args.video, list) else args.video)),
                         offset_seconds=offset_seconds, gpx_path=getattr(args, "gpx", None),
                         sources=sources)
        music = getattr(args, "music", None)
        target = os.path.join(workdir, "combined.mp4") if music else output
        _concat_intro_and_reel(intro, reel_path, cfg.intro_duration, target, W, H)
        if music:
            _apply_music(target, output, args, cfg)
        return output
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
