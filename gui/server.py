from __future__ import annotations

import json
import os
import subprocess
import sys

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

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


app.mount("/static", StaticFiles(directory=STATIC), name="static")
