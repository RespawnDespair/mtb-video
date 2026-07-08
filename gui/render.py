from __future__ import annotations

import sys


def build_render_argv(cfg: dict) -> list:
    """Turn a GUI config dict into a main.py argv (only present flags are added)."""
    argv = [sys.executable, "main.py"]
    if cfg.get("videos"):
        argv += ["--video", *cfg["videos"]]
    if cfg.get("gpx"):
        argv += ["--gpx", cfg["gpx"]]
    if cfg.get("strava_activity_id"):
        argv += ["--strava", "--strava-activity-id", str(cfg["strava_activity_id"])]
    if cfg.get("sync_offset") is not None:
        argv += ["--sync-offset", str(cfg["sync_offset"])]
    if cfg.get("mode"):
        argv += ["--mode", cfg["mode"]]
    if cfg.get("pick"):
        argv += ["--pick", str(cfg["pick"])]
    if cfg.get("music"):
        argv += ["--music", cfg["music"]]
    if cfg.get("output_height"):
        argv += ["--output-height", str(cfg["output_height"])]
    if cfg.get("output_dir"):
        argv += ["--output-dir", cfg["output_dir"]]
    if cfg.get("output"):
        argv += ["--output", cfg["output"]]
    return argv
