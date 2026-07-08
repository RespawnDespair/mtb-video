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


def _gpx_for(activity_id, gpx):
    from highlight_detector import load_gpx
    import strava_gpx
    return load_gpx(gpx) if gpx else strava_gpx.gpx_from_strava(activity_id)


@app.get("/api/timeline")
def timeline(activity_id: str = None, gpx: str = None, offset: float = 0.0, videos: str = ""):
    from config import Config
    g = _gpx_for(activity_id, gpx)
    cfg = Config()
    speeds = list(g.speeds_kmh or [])
    dur = len(speeds)
    n = min(300, dur) or 0
    prof = [max(speeds[i * dur // n: max(i * dur // n + 1, (i + 1) * dur // n)]) for i in range(n)] if n else []
    segs = []
    if activity_id:
        try:
            import strava_client
            from segment_detector import parse_efforts, efforts_to_activity_ranges
            efforts = parse_efforts(strava_client.get_segment_efforts(activity_id))
            for i, (a0, a1, name, _st) in enumerate(efforts_to_activity_ranges(efforts, g.start_time, cfg), 1):
                segs.append({"n": i, "name": name, "start": a0, "end": a1})
        except Exception:
            pass
    clips = []
    vids = [v for v in videos.split("|") if v]
    if vids:
        from clip_sources import build_clip_sources
        for s in build_clip_sources(vids, g, cfg, offset_override=offset):
            clips.append({"file": os.path.basename(s.path), "path": s.path, "base": s.base_offset,
                          "start": s.base_offset, "end": s.base_offset + s.duration})
    return {"duration": dur, "speed": prof, "segments": segs, "clips": clips}


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
        finally:
            p.wait()
            _render["proc"] = None
            yield f"\n[exit {p.returncode}]\n"

    return StreamingResponse(stream(), media_type="text/plain")


@app.post("/api/render/stop")
def render_stop():
    p = _render["proc"]
    if p and p.poll() is None:
        p.terminate()
        return {"stopped": True}
    return {"stopped": False}


app.mount("/static", StaticFiles(directory=STATIC), name="static")
