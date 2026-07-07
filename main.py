from __future__ import annotations

import argparse
import os
import shutil
import sys

from config import Config
from highlight_detector import load_gpx, analyze_video
from video_editor import check_ffmpeg

_BLOCKS = "▁▂▃▄▅▆▇█"


def render_sparkline(values, width: int) -> str:
    """Map a numeric series to block characters, bucketed into `width` columns."""
    if not values:
        return ""
    n = len(values)
    cols = min(width, n)
    # bucket means
    buckets = []
    for i in range(cols):
        lo = (i * n) // cols
        hi = max(lo + 1, ((i + 1) * n) // cols)
        seg = values[lo:hi]
        buckets.append(sum(seg) / len(seg))
    lo_v = min(buckets)
    hi_v = max(buckets)
    if hi_v - lo_v < 1e-9:
        return _BLOCKS[0] * cols
    out = []
    for v in buckets:
        idx = int((v - lo_v) / (hi_v - lo_v) * (len(_BLOCKS) - 1))
        out.append(_BLOCKS[idx])
    return "".join(out)


def resolve_offset_args(args):
    """(offset_override, use_auto) from --sync-offset / --auto-sync."""
    override = getattr(args, "sync_offset", None)
    return (override, bool(getattr(args, "auto_sync", False)))


def resolve_gpx_source(args):
    """Pick the GPX source: --gpx wins; else build from Strava; else error."""
    if args.gpx:
        return load_gpx(args.gpx)
    import strava_client
    if getattr(args, "strava_activity_id", None) and strava_client.is_configured():
        import strava_gpx
        print(f"GPX: opgebouwd uit Strava-activity {args.strava_activity_id}", file=sys.stderr)
        try:
            return strava_gpx.gpx_from_strava(args.strava_activity_id)
        except Exception as e:
            raise SystemExit(f"Kon de Strava-track niet ophalen: {e}")
    raise SystemExit("Geef --gpx of --strava-activity-id (met Strava geconfigureerd).")


def _mmss(seconds) -> str:
    """Format a video time in seconds as M:SS (e.g. 250.0 -> '4:10')."""
    total = int(round(seconds))
    return f"{total // 60}:{total % 60:02d}"


def parse_pick(spec, count):
    """Parse '1,3' into sorted valid 1-based indices within [1, count].

    Returns None when spec is empty/None (meaning: keep all), otherwise the
    de-duplicated, in-range indices (possibly an empty list if none are valid).
    """
    if not spec:
        return None
    picks = []
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            continue
        try:
            n = int(part)
        except ValueError:
            continue
        if 1 <= n <= count and n not in picks:
            picks.append(n)
    return sorted(picks)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Generate an MTB highlight video from action-cam footage + GPX telemetry."
    )
    p.add_argument("--video", required=True, help="Path to the source video file.")
    p.add_argument("--gpx", help="Path to the Strava/Garmin GPX export. If omitted, "
                                 "the track is built from --strava-activity-id.")
    p.add_argument("--music", help="Path to a royalty-free MP3/AAC music track.")
    p.add_argument("--output", default="highlight.mp4", help="Output video path.")
    p.add_argument("--output-height", default="1080",
                   help="Output video height in pixels (default 1080), or 'source' to "
                        "match the input. Width follows the source aspect. e.g. 2160 for 4K.")
    p.add_argument("--dry-run", action="store_true",
                   help="Print detected segments and scores; render nothing.")
    p.add_argument("--cut-mode", choices=["reencode", "copy"], default="reencode")
    p.add_argument("--min-speed", type=float, help="Min speed km/h (standstill filter).")
    p.add_argument("--score-cutoff", type=float, help="Per-second interest score cutoff.")
    p.add_argument("--strava", action="store_true", help="Enrich intro with Strava stats.")
    p.add_argument("--strava-activity-id", help="Strava activity ID for stats.")
    p.add_argument("--garmin", action="store_true", help="Enrich intro with Garmin stats.")
    p.add_argument("--inspect", action="store_true",
                   help="Print a sync/segment diagnosis report and render nothing.")
    p.add_argument("--sync-offset", type=float, default=None,
                   help="Force the sync offset in seconds (video_t -> activity_t + offset).")
    p.add_argument("--auto-sync", action="store_true",
                   help="Use motion/GPS cross-correlation to pick the offset for rendering.")
    p.add_argument("--mode", choices=["auto", "segments", "flow"], default="auto",
                   help="Highlight source: auto (segments if available else flow), "
                        "segments (Strava segments only), or flow (speed/motion).")
    p.add_argument("--pick", default=None,
                   help="Comma-separated 1-based segment numbers to render (from the "
                        "--dry-run list), e.g. --pick 1,3. Default: all segments.")
    p.add_argument("--music-loop-start", type=float, default=None,
                   help="Manual music loop-in point (s); overrides auto-detection "
                        "(use with --music-loop-end).")
    p.add_argument("--music-loop-end", type=float, default=None,
                   help="Manual music loop-out point (s); overrides auto-detection.")
    return p


def build_config_from_args(args) -> Config:
    cfg = Config(cut_mode=args.cut_mode)
    if args.min_speed is not None:
        cfg.min_speed_kmh = args.min_speed
    if args.score_cutoff is not None:
        cfg.score_cutoff = args.score_cutoff
    return cfg


def resolve_output_height(value, video_path) -> int:
    """Resolve --output-height: 'source' -> the source video height; else int."""
    if isinstance(value, str) and value.strip().lower() == "source":
        from highlight_detector import get_video_resolution
        return get_video_resolution(video_path)[1]
    try:
        return int(value)
    except (TypeError, ValueError):
        raise SystemExit(f"--output-height must be an integer or 'source', got {value!r}")


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


def print_inspection(analysis, gpx) -> None:
    a = analysis
    local = a.video_creation_time.astimezone()
    print("=== GPX ===")
    print(f"  activity start : {gpx.start_time.isoformat()} (UTC)")
    print(f"  duration       : {len(gpx.speeds_kmh)} s")
    print(f"  distance       : {gpx.total_distance_km:.2f} km")
    print(f"  elevation gain : {gpx.elevation_gain_m:.0f} m")
    print(f"  moving time    : {gpx.moving_time_s / 60:.1f} min")
    print("\n=== Video ===")
    print(f"  creation_time  : {a.video_creation_time.isoformat()} "
          f"({'metadata' if a.from_metadata else 'file mtime — unreliable'})")
    print(f"  local time     : {local.isoformat()}")
    print(f"  duration       : {a.video_duration:.0f} s   fps: {a.fps:.2f}")
    print("\n=== Sync ===")
    print(f"  metadata offset: {a.metadata_offset:+.1f} s")
    print(f"  auto offset    : {a.auto_offset:+.1f} s   (correlation {a.auto_correlation:+.2f})")
    print(f"  USING          : {a.offset_used:+.1f} s   (source: {a.offset_source})")
    fo = a.filename_offset
    print(f"  filename offset: {('%+.1f s' % fo) if fo is not None else 'n/a'}")
    if a.offset_source == "auto" and abs(a.auto_correlation) < 0.3:
        print("  ! low correlation — the auto-aligned offset is uncertain; verify the "
              "sparklines or set --sync-offset manually.")

    width = 100
    dur = int(a.video_duration)
    speed_row = a.speeds_at_video[:dur]
    flow_row = a.flow_per_sec[:dur]
    score_row = a.scores[:dur]
    print("\n=== Aligned on VIDEO time (offset applied) ===")
    print(f"  0s{' ' * (width - 6)}{dur}s")
    print(f"  speed  {render_sparkline(speed_row, width)}")
    print(f"  motion {render_sparkline(flow_row, width)}")
    print(f"  score  {render_sparkline(score_row, width)}")
    total = sum(seg.end - seg.start for seg in a.segments)
    print(f"\n  segments kept: {len(a.segments)}  ({total:.0f}s of {dur}s)")
    for seg in a.segments:
        print(f"    {seg.start:7.1f}s -> {seg.end:7.1f}s  score={seg.score:0.2f}")


def select_segment_clips(args, gpx, cfg, offset_seconds):
    """Fetch + map Strava segment efforts to clips, or None if unavailable."""
    from highlight_detector import get_video_duration
    try:
        import strava_client
        from segment_detector import parse_efforts, efforts_to_clips
        if not strava_client.is_configured() or not args.strava_activity_id:
            return None
        raw = strava_client.get_segment_efforts(args.strava_activity_id)
        efforts = parse_efforts(raw)
        duration = get_video_duration(args.video)
        clips = efforts_to_clips(efforts, gpx.start_time, offset_seconds, duration, cfg)
        return clips or None
    except Exception as e:
        print(f"[warn] Strava segments unavailable: {e}", file=sys.stderr)
        return None


def main() -> int:
    args = build_parser().parse_args()
    check_ffmpeg()
    cfg = build_config_from_args(args)
    cfg.output_height = resolve_output_height(args.output_height, args.video)

    gpx = resolve_gpx_source(args)
    offset_override, use_auto = resolve_offset_args(args)

    from highlight_detector import resolve_sync_offset

    if args.inspect:
        analysis = analyze_video(args.video, gpx, cfg,
                                 offset_override=offset_override, use_auto=use_auto)
        if analysis.offset_source == "mtime":
            print(f"[warn] {args.video} has no creation_time metadata; using file mtime "
                  "for sync — alignment may be approximate.", file=sys.stderr)
        print_inspection(analysis, gpx)
        return 0

    # Resolve the offset flow-free unless --auto-sync (avoids optical flow on big clips).
    r = resolve_sync_offset(args.video, gpx, cfg,
                            offset_override=offset_override, use_auto=use_auto)
    if r.offset_source == "mtime":
        print(f"[warn] {args.video} has no creation_time metadata; using file mtime "
              "for sync — alignment may be approximate.", file=sys.stderr)

    clips = None
    if args.mode in ("auto", "segments"):
        clips = select_segment_clips(args, gpx, cfg, r.offset_used)
        if args.mode == "segments" and not clips:
            print("Segment mode requested but no Strava segments available "
                  "(need --strava-activity-id, configured Strava, and segments in "
                  "the video window).", file=sys.stderr)
            return 1

    if clips:  # segment mode
        from segment_detector import format_segment_stats
        picks = parse_pick(getattr(args, "pick", None), len(clips))
        numbered = list(enumerate(clips, 1))  # (number, clip) in chronological order

        if args.dry_run:
            print(f"Segment highlights ({len(clips)}):")
            for n, c in numbered:
                mark = "" if picks is None or n in picks else "   (skipped)"
                print(f"  {n}. {_mmss(c.start):>6} -> {_mmss(c.end):>6}  {c.name}  "
                      f"[{format_segment_stats(c)}]{mark}")
            return 0

        if picks is not None:
            numbered = [(n, c) for n, c in numbered if n in picks]
            if not numbered:
                print("--pick matched no segments; nothing to render.", file=sys.stderr)
                return 1
        clips = [c for _, c in numbered]
        print(f"Rendering {len(clips)} segment(s):", file=sys.stderr)
        for n, c in numbered:
            print(f"  {n}. {_mmss(c.start):>6} -> {_mmss(c.end):>6}  {c.name}  "
                  f"[{format_segment_stats(c)}]", file=sys.stderr)
        from video_editor import build_segment_reel
        from intro_generator import build_final_video
        telemetry_source = None
        try:
            import strava_client
            from telemetry import telemetry_from_streams
            if strava_client.is_configured() and args.strava_activity_id:
                streams = strava_client.get_activity_streams(args.strava_activity_id)
                if streams:
                    telemetry_source = telemetry_from_streams(streams, gpx)
                    print("Telemetrie: Strava-streams", file=sys.stderr)
        except Exception as e:
            print(f"[warn] Strava streams unavailable ({e}); using GPX telemetry.",
                  file=sys.stderr)
        reel = build_segment_reel(args.video, clips, None, cfg,
                                  gpx=gpx, offset_seconds=r.offset_used,
                                  telemetry_source=telemetry_source)
        try:
            print("Intro + eindmontage renderen…", file=sys.stderr)
            build_final_video(reel, gpx, cfg, args.output, args, offset_seconds=r.offset_used)
        finally:
            import os, shutil
            shutil.rmtree(os.path.dirname(reel), ignore_errors=True)
        print(f"Wrote {args.output}")
        return 0

    # flow mode (fallback / --mode flow)
    analysis = analyze_video(args.video, gpx, cfg,
                             offset_override=offset_override, use_auto=use_auto)
    segments, scores = analysis.segments, analysis.scores
    if args.dry_run:
        print_segments(segments, scores)
        return 0
    if not segments:
        print("No highlight segments detected; nothing to render.", file=sys.stderr)
        return 1
    from video_editor import build_highlight_reel
    from intro_generator import build_final_video
    reel = build_highlight_reel(args.video, segments, None, cfg)
    try:
        print("Intro + eindmontage renderen…", file=sys.stderr)
        build_final_video(reel, gpx, cfg, args.output, args, offset_seconds=r.offset_used)
    finally:
        import os, shutil
        shutil.rmtree(os.path.dirname(reel), ignore_errors=True)
    print(f"Wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
