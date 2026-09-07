# SPDX-License-Identifier: GPL-3.0-or-later
"""Convert scheduler state between versions without executing jobs."""


def to_v1(data):
    """Pause and drop v2-only fields so a v1 service can read the store."""
    jobs = []
    for job in data.get("jobs", []):
        if not isinstance(job, dict):
            continue
        action = job.get("action")
        enabled = bool(job.get("enabled"))
        if action not in ("on", "off", "wol"):
            action = "off"
            enabled = False
        repeat = job.get("repeat", "once")
        if repeat not in ("once", "daily", "weekly"):
            repeat = "once"
        identity = job.get("id")
        name = job.get("name")
        when = job.get("at")
        if not isinstance(identity, str) or not isinstance(name, str) or not isinstance(when, str):
            continue
        mac = job.get("mac", "") if action == "wol" else ""
        if not isinstance(mac, str):
            mac = ""
        jobs.append({
            "id": identity,
            "name": name,
            "action": action,
            "at": when,
            "repeat": repeat,
            "mac": mac,
            "enabled": enabled,
        })
        if len(jobs) >= 64:
            break
    history = []
    for event in data.get("history", []):
        if not isinstance(event, dict):
            continue
        history.append({
            "time": str(event.get("time", "")),
            "job_id": str(event.get("job_id", "")),
            "name": str(event.get("name", "")),
            "status": str(event.get("status", "")),
            "detail": str(event.get("detail", "")),
        })
        if len(history) >= 100:
            break
    return {"version": 1, "armed": False, "jobs": jobs, "history": history}
