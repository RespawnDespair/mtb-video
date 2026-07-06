from __future__ import annotations

import argparse
import os
import shutil
import sys

from config import Config
from highlight_detector import load_gpx, detect_highlights
from video_editor import check_ffmpeg


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Generate an MTB highlight video from action-cam footage + GPX telemetry."
    )
    p.add_argument("--video", required=True, help="Path to the source video file.")
    p.add_argument("--gpx", required=True, help="Path to the Strava/Garmin GPX export.")
    p.add_argument("--music", help="Path to a royalty-free MP3/AAC music track.")
    p.add_argument("--output", default="highlight.mp4", help="Output video path.")
    p.add_argument("--dry-run", action="store_true",
                   help="Print detected segments and scores; render nothing.")
    p.add_argument("--cut-mode", choices=["reencode", "copy"], default="reencode")
    p.add_argument("--min-speed", type=float, help="Min speed km/h (standstill filter).")
    p.add_argument("--score-cutoff", type=float, help="Per-second interest score cutoff.")
    p.add_argument("--strava", action="store_true", help="Enrich intro with Strava stats.")
    p.add_argument("--strava-activity-id", help="Strava activity ID for stats.")
    p.add_argument("--garmin", action="store_true", help="Enrich intro with Garmin stats.")
    return p


def build_config_from_args(args) -> Config:
    cfg = Config(cut_mode=args.cut_mode)
    if args.min_speed is not None:
        cfg.min_speed_kmh = args.min_speed
    if args.score_cutoff is not None:
        cfg.score_cutoff = args.score_cutoff
    return cfg


def print_segments(segments, scores) -> None:
    print(f"\nPer-second interest scores ({len(scores)}s):")
    for t, s in enumerate(scores):
        bar = "#" * int(s * 20)
        print(f"  {t:4d}s  {s:0.2f} {bar}")
    print(f"\nDetected {len(segments)} segment(s) to keep:")
    total = 0.0
    for seg in segments:
        dur = seg.end - seg.start
        total += dur
        print(f"  {seg.start:7.1f}s -> {seg.end:7.1f}s  ({dur:4.1f}s)  score={seg.score:0.2f}")
    print(f"\nTotal kept: {total:.1f}s across {len(segments)} segment(s).")


def main() -> int:
    args = build_parser().parse_args()
    check_ffmpeg()
    cfg = build_config_from_args(args)

    gpx = load_gpx(args.gpx)
    segments, scores = detect_highlights(args.video, gpx, cfg)

    if args.dry_run:
        print_segments(segments, scores)
        return 0

    if not segments:
        print("No highlight segments detected; nothing to render.", file=sys.stderr)
        return 1

    # Stages 2 & 3 wired in Task 8.
    from video_editor import build_highlight_reel
    from intro_generator import build_final_video
    reel = build_highlight_reel(args.video, segments, args.music, cfg)
    try:
        build_final_video(reel, gpx, cfg, args.output, args)
    finally:
        shutil.rmtree(os.path.dirname(reel), ignore_errors=True)
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
