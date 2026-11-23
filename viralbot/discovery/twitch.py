"""Twitch discovery via the official Helix API (app access token).

Finds the most-viewed recent clips for configured games/categories and for
authorized broadcasters. Third-party clips are stored as *leads* only; clips
from broadcasters in AUTHORIZED_TWITCH_BROADCASTERS are marked authorized.
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import requests

from ..config import env
from ..logging_setup import get_logger

log = get_logger("discovery.twitch")
HELIX = "https://api.twitch.tv/helix"
_token: dict = {}


def _app_token() -> str:
    e = env()
    if _token.get("value") and _token.get("exp", 0) > time.time() + 60:
        return _token["value"]
    r = requests.post("https://id.twitch.tv/oauth2/token", data={
        "client_id": e.twitch_client_id, "client_secret": e.twitch_client_secret,
        "grant_type": "client_credentials"}, timeout=30)
    r.raise_for_status()
    data = r.json()
    _token.update(value=data["access_token"], exp=time.time() + data.get("expires_in", 3600))
    return _token["value"]


def _get(path: str, params: dict) -> dict:
    headers = {"Client-Id": env().twitch_client_id, "Authorization": f"Bearer {_app_token()}"}
    for attempt in range(3):
        r = requests.get(f"{HELIX}/{path}", params=params, headers=headers, timeout=30)
        if r.status_code == 429:
            reset = float(r.headers.get("Ratelimit-Reset", time.time() + 2))
            time.sleep(min(30, max(1, reset - time.time())))
            continue
        r.raise_for_status()
        return r.json()
    raise RuntimeError("Twitch rate limit")


def _clip_item(c: dict, topic: str) -> dict:
    authorized = c.get("broadcaster_name", "").lower() in env().authorized_twitch_broadcasters or \
        c.get("broadcaster_id", "") in env().authorized_twitch_broadcasters
    item = {
        "platform": "twitch",
        "external_id": c["id"],
        "url": c.get("url"),
        "title": c.get("title", ""),
        "channel_id": c.get("broadcaster_id"),
        "channel_title": c.get("broadcaster_name", ""),
        "published_at": c.get("created_at"),
        "duration_s": float(c.get("duration") or 0),
        "views": int(c.get("view_count") or 0),
        "likes": 0, "comments": 0,
        "topic": topic,
        "raw": {"game_id": c.get("game_id"), "language": c.get("language"), "creator": c.get("creator_name")},
        "rights_status": "authorized" if authorized else "unknown",
    }
    if authorized:
        item["rights_evidence"] = "Broadcaster listed in AUTHORIZED_TWITCH_BROADCASTERS (keep written permission on file)"
        item["attribution"] = f"Clip from {c.get('broadcaster_name')} on Twitch: https://twitch.tv/{c.get('broadcaster_name')}"
    return item


def discover(settings: dict) -> list[dict]:
    e = env()
    if not (e.twitch_client_id and e.twitch_client_secret):
        log.info("Twitch credentials not set – skipping Twitch discovery")
        return []
    started = (datetime.now(timezone.utc) - timedelta(days=settings["discovery_lookback_days"])).strftime("%Y-%m-%dT%H:%M:%SZ")
    out: list[dict] = []
    try:
        # authorized broadcasters
        if e.authorized_twitch_broadcasters:
            logins = [b for b in e.authorized_twitch_broadcasters if not b.isdigit()]
            ids = [b for b in e.authorized_twitch_broadcasters if b.isdigit()]
            if logins:
                users = _get("users", {"login": logins[:100]}).get("data", [])
                ids += [u["id"] for u in users]
            for bid in ids:
                for c in _get("clips", {"broadcaster_id": bid, "started_at": started, "first": 20}).get("data", []):
                    out.append(_clip_item(c, "twitch:authorized"))
        # trending clips per game (leads unless authorized)
        names = settings.get("twitch_game_names", [])
        if names:
            games = _get("games", {"name": names[:100]}).get("data", [])
            for g in games:
                clips = _get("clips", {"game_id": g["id"], "started_at": started,
                                       "first": settings["discovery_max_results_per_topic"]}).get("data", [])
                out += [_clip_item(c, f"twitch:{g['name']}") for c in clips if c.get("language", "en").startswith("en")]
    except requests.RequestException as ex:
        log.warning("Twitch discovery failed: %s", ex)
    return out
