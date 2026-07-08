from __future__ import annotations

import os

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


app.mount("/static", StaticFiles(directory=STATIC), name="static")
