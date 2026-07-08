from __future__ import annotations

import json
import os
import time

import requests

TOKEN_FILE = ".strava_token.json"
_API = "https://www.strava.com/api/v3"
_OAUTH = "https://www.strava.com/oauth/token"


def is_configured() -> bool:
    return (os.path.exists(TOKEN_FILE)
            and bool(os.environ.get("STRAVA_CLIENT_ID"))
            and bool(os.environ.get("STRAVA_CLIENT_SECRET")))


def _load_token() -> dict:
    with open(TOKEN_FILE) as f:
        return json.load(f)


def _save_token(token: dict) -> None:
    # Write atomically so a concurrent reader never sees a truncated/half-written file
    # (the GUI can fire several Strava calls at once).
    tmp = f"{TOKEN_FILE}.tmp"
    with open(tmp, "w") as f:
        json.dump(token, f)
    os.replace(tmp, TOKEN_FILE)


def _refresh_if_needed(token: dict, now_epoch: float) -> dict:
    if token.get("expires_at", 0) > now_epoch:
        return token
    resp = requests.post(_OAUTH, data={
        "client_id": os.environ["STRAVA_CLIENT_ID"],
        "client_secret": os.environ["STRAVA_CLIENT_SECRET"],
        "grant_type": "refresh_token",
        "refresh_token": token["refresh_token"],
    }, timeout=30)
    resp.raise_for_status()
    new = resp.json()
    token.update({
        "access_token": new["access_token"],
        "refresh_token": new["refresh_token"],
        "expires_at": new["expires_at"],
    })
    return token


def get_activity_stats(activity_id: str | None) -> dict:
    if not is_configured():
        raise RuntimeError("Strava not configured (.strava_token.json + env vars).")
    token = _refresh_if_needed(_load_token(), now_epoch=time.time())
    _save_token(token)
    headers = {"Authorization": f"Bearer {token['access_token']}"}
    if not activity_id:
        acts = requests.get(f"{_API}/athlete/activities", headers=headers,
                            params={"per_page": 1}, timeout=30).json()
        if not acts:
            return {}
        activity_id = acts[0]["id"]
    a = requests.get(f"{_API}/activities/{activity_id}", headers=headers, timeout=30).json()
    return {
        "avg_hr": a.get("average_heartrate"),
        "avg_power": a.get("average_watts"),
        "max_speed_kmh": (a.get("max_speed") or 0) * 3.6,
    }


def get_segment_efforts(activity_id: str) -> list:
    """Return the activity's segment_efforts (each with timing + stats)."""
    if not is_configured():
        raise RuntimeError("Strava not configured (.strava_token.json + env vars).")
    token = _refresh_if_needed(_load_token(), now_epoch=time.time())
    _save_token(token)
    headers = {"Authorization": f"Bearer {token['access_token']}"}
    resp = requests.get(f"{_API}/activities/{activity_id}", headers=headers,
                        params={"include_all_efforts": True}, timeout=30)
    resp.raise_for_status()
    return resp.json().get("segment_efforts", []) or []


_STREAM_KEYS = "time,latlng,distance,altitude,velocity_smooth,heartrate,watts,grade_smooth"


def get_activity(activity_id: str) -> dict:
    """Return the raw Strava activity JSON (name, start_date, totals, ...)."""
    if not is_configured():
        raise RuntimeError("Strava not configured (.strava_token.json + env vars).")
    token = _refresh_if_needed(_load_token(), now_epoch=time.time())
    _save_token(token)
    headers = {"Authorization": f"Bearer {token['access_token']}"}
    resp = requests.get(f"{_API}/activities/{activity_id}", headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.json() or {}


def get_activity_streams(activity_id: str) -> dict:
    """Return the activity's per-second streams keyed by type."""
    if not is_configured():
        raise RuntimeError("Strava not configured (.strava_token.json + env vars).")
    token = _refresh_if_needed(_load_token(), now_epoch=time.time())
    _save_token(token)
    headers = {"Authorization": f"Bearer {token['access_token']}"}
    resp = requests.get(f"{_API}/activities/{activity_id}/streams", headers=headers,
                        params={"keys": _STREAM_KEYS, "key_by_type": True}, timeout=30)
    resp.raise_for_status()
    return resp.json() or {}


def list_activities(n: int = 15) -> list:
    """Recent activities as summary dicts for the GUI picker."""
    if not is_configured():
        raise RuntimeError("Strava not configured (.strava_token.json + env vars).")
    token = _refresh_if_needed(_load_token(), now_epoch=time.time())
    _save_token(token)
    headers = {"Authorization": f"Bearer {token['access_token']}"}
    acts = requests.get(f"{_API}/athlete/activities", headers=headers,
                        params={"per_page": n}, timeout=30).json()
    out = []
    for a in acts or []:
        out.append({
            "id": a.get("id"), "name": a.get("name") or "Activity",
            "start_date": a.get("start_date"), "type": a.get("type"),
            "distance_km": round((a.get("distance") or 0) / 1000, 1),
            "moving_time_s": a.get("moving_time") or 0,
            "elevation_gain_m": round(a.get("total_elevation_gain") or 0),
        })
    return out
