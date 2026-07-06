from __future__ import annotations

import os
from datetime import datetime


def is_configured() -> bool:
    return bool(os.environ.get("GARMIN_EMAIL")) and bool(os.environ.get("GARMIN_PASSWORD"))


def get_activity_stats(activity_start: datetime) -> dict:
    """Fetch HR/power for the activity on the given date. Raises if unconfigured."""
    if not is_configured():
        raise RuntimeError("Garmin not configured (set GARMIN_EMAIL / GARMIN_PASSWORD).")
    from garminconnect import Garmin

    client = Garmin(os.environ["GARMIN_EMAIL"], os.environ["GARMIN_PASSWORD"])
    client.login()
    date_str = activity_start.strftime("%Y-%m-%d")
    activities = client.get_activities_by_date(date_str, date_str)
    if not activities:
        return {}
    a = activities[0]
    return {
        "avg_hr": a.get("averageHR"),
        "avg_power": a.get("avgPower"),
    }
