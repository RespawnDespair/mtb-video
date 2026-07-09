from __future__ import annotations

import json
import os
import subprocess
import sys

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from gui.render import build_render_argv

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

app = FastAPI(title="Ride Highlight Editor")


@app.get("/")
def index():
    return FileResponse(os.path.join(STATIC, "index.html"))


@app.get("/api/activities")
def activities():
    import strava_client
    try:
        return strava_client.list_activities()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Strava niet beschikbaar: {e}")


def _probe(path):
    r = subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json",
                        "-show_format", "-show_streams", path], capture_output=True, text=True)
    info = json.loads(r.stdout or "{}")
    v = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), {})
    dur = float(info.get("format", {}).get("duration", 0) or 0)
    return {"path": path, "name": os.path.basename(path), "duration": dur,
            "width": int(v.get("width", 0)), "height": int(v.get("height", 0))}


@app.post("/api/videos")
def videos(body: dict):
    from main import gather_videos_in_dir
    paths = []
    if body.get("dir"):
        paths = gather_videos_in_dir(body["dir"])
    paths += [p for p in body.get("paths", []) if os.path.isfile(p)]
    return [_probe(p) for p in paths]


_AUDIO = (".mp3", ".m4a", ".aac", ".wav")


@app.get("/api/music")
def music(dir: str):
    if not os.path.isdir(dir):
        raise HTTPException(404, f"map niet gevonden: {dir}")
    subs = []
    for name in sorted(os.listdir(dir)):
        p = os.path.join(dir, name)
        if os.path.isdir(p):
            cnt = len([f for f in os.listdir(p) if f.lower().endswith(_AUDIO)])
            if cnt:
                subs.append({"name": name, "path": p, "count": cnt})
    here = len([f for f in os.listdir(dir) if f.lower().endswith(_AUDIO)])
    return {"dir": dir, "here": here, "subfolders": subs}


def _native_pick(mode: str):
    if sys.platform == "darwin":
        prompts = {"folder": "choose folder",
                   "files": "choose file with multiple selections allowed",
                   "gpx": 'choose file of type {"gpx","xml"}'}
        scr = (f'set sel to ({prompts[mode]})\n'
               'if class of sel is list then\n set out to ""\n'
               ' repeat with f in sel\n  set out to out & POSIX path of f & "\\n"\n end repeat\n'
               ' return out\nelse\n return POSIX path of sel\nend if')
        r = subprocess.run(["osascript", "-e", scr], capture_output=True, text=True)
        if r.returncode != 0:
            return None
        return [p for p in r.stdout.strip().split("\n") if p]
    # non-macOS fallback
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk(); root.withdraw()
    if mode == "folder":
        p = filedialog.askdirectory()
        res = [p] if p else None
    elif mode == "gpx":
        p = filedialog.askopenfilename(filetypes=[("GPX", "*.gpx *.xml")])
        res = [p] if p else None
    else:
        p = filedialog.askopenfilenames()
        res = list(p) or None
    root.destroy()
    return res


@app.post("/api/pick")
def pick(body: dict):
    mode = body.get("mode", "folder")
    if mode not in ("folder", "files", "gpx"):
        raise HTTPException(400, "onbekende modus")
    paths = _native_pick(mode)
    if not paths:
        return {"cancelled": True}
    return {"path": paths[0]} if mode in ("folder", "gpx") else {"paths": paths}


_gpx_cache: dict = {}


def _gpx_for(activity_id, gpx):
    from highlight_detector import load_gpx
    import strava_gpx
    return load_gpx(gpx) if gpx else strava_gpx.gpx_from_strava(activity_id)


def _gpx_cached(activity_id, gpx):
    """Memoise the built GpxData per source so per-offset / per-segment requests don't
    refetch from Strava."""
    key = (activity_id or "", gpx or "")
    if key not in _gpx_cache:
        _gpx_cache[key] = _gpx_for(activity_id, gpx)
    return _gpx_cache[key]


@app.get("/api/track")
def track(activity_id: str = None, gpx: str = None):
    """Ride coordinates as JSON, indexed by activity-second (coords[i] = second i).
    The client projects these (a JS port of minimap.project) and draws the minimap
    SVGs + the scrub dot, so scrubbing needs no per-tick server round-trip."""
    g = _gpx_cached(activity_id, gpx)
    return {"coords": [[la, lo] for la, lo in (g.coords or [])]}


_timeline_cache: dict = {}


@app.get("/api/timeline")
def timeline(activity_id: str = None, gpx: str = None, offset: float = 0.0, videos: str = ""):
    # Only `offset` changes while dragging; the track, profile, segments, and each
    # video's own recording offset don't. Build those once per (source, videos) and
    # cache them, so a slider drag does no Strava/ffprobe work (and can't race on the
    # token file) — clip positions are then a pure arithmetic shift by the offset.
    key = (activity_id or "", gpx or "", videos)
    if key not in _timeline_cache:
        from config import Config
        from segment_detector import parse_efforts, efforts_to_activity_ranges
        g = _gpx_cached(activity_id, gpx)
        cfg = Config()
        speeds = list(g.speeds_kmh or [])
        dur = len(speeds)
        n = min(300, dur)
        prof = [max(speeds[i * dur // n: max(i * dur // n + 1, (i + 1) * dur // n)])
                for i in range(n)] if n else []
        segs = []
        if activity_id:
            try:
                import strava_client
                efforts = parse_efforts(strava_client.get_segment_efforts(activity_id))
                for i, (a0, a1, name, _st) in enumerate(
                        efforts_to_activity_ranges(efforts, g.start_time, cfg), 1):
                    segs.append({"n": i, "name": name, "start": a0, "end": a1})
            except Exception:
                pass
        clip_meta, ref = [], 0.0
        vids = [v for v in videos.split("|") if v]
        if vids:
            from clip_sources import build_clip_sources
            bases = build_clip_sources(vids, g, cfg, offset_override=None)
            ref = min((s.base_offset for s in bases), default=0.0)
            clip_meta = [{"file": os.path.basename(s.path), "path": s.path,
                          "own": s.base_offset, "dur": s.duration} for s in bases]
        _timeline_cache[key] = {"duration": dur, "speed": prof, "segments": segs,
                                "ref": ref, "clip_meta": clip_meta}
    c = _timeline_cache[key]
    # base_i = offset + (own_i - ref) — identical to build_clip_sources(offset_override=offset)
    clips = [{"file": m["file"], "path": m["path"],
              "base": offset + (m["own"] - c["ref"]),
              "start": offset + (m["own"] - c["ref"]),
              "end": offset + (m["own"] - c["ref"]) + m["dur"]} for m in c["clip_meta"]]
    # Render parts — one per (segment × covering clip), via the SAME resolve_render_parts
    # the CLI uses, so the previews match the render (a segment spanning two files → two
    # parts). Computed per request since coverage moves with the offset.
    import types
    from clip_sources import resolve_render_parts
    srcs = [types.SimpleNamespace(path=m["path"], base_offset=offset + (m["own"] - c["ref"]),
                                  duration=m["dur"]) for m in c["clip_meta"]]
    ranges = [(s["start"], s["end"], s["name"], {"n": s["n"]}) for s in c["segments"]]
    rparts, _ = resolve_render_parts(srcs, ranges)
    parts = [{"n": p.stats["n"], "name": p.name, "file": os.path.basename(p.source_path),
              "path": p.source_path, "a0": p.base_offset + p.local_start,
              "a1": p.base_offset + p.local_end, "local": p.local_start} for p in rparts]
    return {"duration": c["duration"], "speed": c["speed"], "segments": c["segments"],
            "clips": clips, "parts": parts}


_frame_cache: dict = {}


@app.get("/api/frame")
def frame(video: str, t: float, w: int = 320):
    if not os.path.isfile(video):
        raise HTTPException(404, "video niet gevonden")
    key = (video, round(t), w)
    if key not in _frame_cache:
        r = subprocess.run(["ffmpeg", "-v", "error", "-ss", str(max(0.0, t)), "-i", video,
                            "-frames:v", "1", "-vf", f"scale={w}:-2", "-q:v", "4",
                            "-f", "mjpeg", "pipe:1"], capture_output=True)
        if r.returncode != 0 or not r.stdout:
            raise HTTPException(404, "geen frame op deze tijd")
        if len(_frame_cache) > 200:
            _frame_cache.pop(next(iter(_frame_cache)))
        _frame_cache[key] = r.stdout
    return Response(_frame_cache[key], media_type="image/jpeg")


@app.post("/api/auto-align")
def auto_align(body: dict):
    from config import Config
    from highlight_detector import compute_optical_flow_per_second, estimate_offset_by_motion
    g = _gpx_for(body.get("activity_id"), body.get("gpx"))
    cfg = Config()
    flow = compute_optical_flow_per_second(body["video"], cfg)
    off, corr = estimate_offset_by_motion(flow, g, cfg)
    return {"offset": off, "correlation": corr}


_render = {"proc": None}


@app.post("/api/render")
def render(cfg: dict):
    if _render["proc"] and _render["proc"].poll() is None:
        raise HTTPException(409, "er loopt al een render")
    argv = build_render_argv(cfg)

    def stream():
        yield "$ " + " ".join(argv) + "\n"
        p = subprocess.Popen(argv, cwd=REPO, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, text=True, bufsize=1)
        _render["proc"] = p
        try:
            for line in p.stdout:
                yield line
            p.wait()
            yield f"\n[exit {p.returncode}]\n"
        finally:
            # cleanup only — no yield here, so a client disconnect (GeneratorExit)
            # can't trigger "generator ignored GeneratorExit".
            if p.poll() is None:
                p.terminate()
                p.wait()
            _render["proc"] = None

    return StreamingResponse(stream(), media_type="text/plain")


@app.post("/api/render/stop")
def render_stop():
    p = _render["proc"]
    if p and p.poll() is None:
        p.terminate()
        return {"stopped": True}
    return {"stopped": False}


app.mount("/static", StaticFiles(directory=STATIC), name="static")
