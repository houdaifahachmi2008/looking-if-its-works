"""Audience as jury: moments that viewers themselves pointed at.

* YouTube: timestamps in comments ("3:45 😂😂"), weighted by the comment's likes
  (commentThreads.list – 1 quota unit per 100 comments)
* Twitch VODs: clips viewers made of the stream (vod_offset + duration), weighted by views

Signals are stored in ``crowd_signals`` and turned into a 0..1 score per
candidate window by ``window_scores``.
"""
from __future__ import annotations

import math
import re
from datetime import datetime, timedelta

import requests

from . import db
from .config import env
from .logging_setup import get_logger

log = get_logger("crowd")
TS_RE = re.compile(r"(?<![\d:])(?:(\d{1,2}):)?(\d{1,2}):([0-5]\d)(?![\d:])")


def parse_timestamps(text: str, max_t: float) -> list[float]:
    out = []
    for h, m, s in TS_RE.findall(text or ""):
        t = int(h or 0) * 3600 + int(m) * 60 + int(s)
        if 0 < t <= max_t:
            out.append(float(t))
    return sorted(set(out))


def youtube_comment_signals(video_id: str, duration: float, pages: int = 3) -> list[dict]:
    from .discovery.youtube import QuotaExceeded, _get
    sigs: list[dict] = []
    token = None
    for _ in range(pages):
        params = {"part": "snippet", "videoId": video_id, "maxResults": 100, "order": "relevance",
                  "textFormat": "plainText"}
        if token:
            params["pageToken"] = token
        try:
            data = _get("commentThreads", params)
        except QuotaExceeded:
            break
        except requests.RequestException as ex:  # comments disabled etc.
            log.info("comments unavailable for %s: %s", video_id, str(ex)[:120])
            break
        for it in data.get("items", []):
            sn = it["snippet"]["topLevelComment"]["snippet"]
            ts = parse_timestamps(sn.get("textOriginal") or sn.get("textDisplay") or "", duration)
            w = 1.0 + math.log1p(int(sn.get("likeCount") or 0))
            for t in ts:
                sigs.append({"t": t, "dur": 0.0, "weight": w / len(ts), "kind": "comment"})
        token = data.get("nextPageToken")
        if not token:
            break
    return sigs


def parse_twitch_duration(s: str | None) -> float:
    m = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", s or "")
    if not m or not s:
        return 0.0
    h, mi, se = (int(x or 0) for x in m.groups())
    return h * 3600 + mi * 60 + se


def twitch_vod_signals(raw: dict) -> list[dict]:
    from .discovery.twitch import _get
    vod_id, user_id, created = raw.get("vod_id"), raw.get("user_id"), raw.get("created_at")
    if not (vod_id and user_id and created):
        return []
    start = datetime.fromisoformat(created.replace("Z", "+00:00"))
    end = start + timedelta(seconds=parse_twitch_duration(raw.get("duration")) + 3600)
    sigs, cursor = [], None
    for _ in range(5):
        params = {"broadcaster_id": user_id, "started_at": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                  "ended_at": end.strftime("%Y-%m-%dT%H:%M:%SZ"), "first": 100}
        if cursor:
            params["after"] = cursor
        data = _get("clips", params)
        for c in data.get("data", []):
            if str(c.get("video_id")) == str(vod_id) and c.get("vod_offset") is not None:
                sigs.append({"t": float(c["vod_offset"]), "dur": float(c.get("duration") or 30),
                             "weight": 1.0 + math.log1p(int(c.get("view_count") or 0)), "kind": "twitch_clip"})
        cursor = (data.get("pagination") or {}).get("cursor")
        if not cursor:
            break
    return sigs


def collect(source: dict) -> int:
    """Fetch and store crowd signals for a source. Returns number of signals (0 if not applicable)."""
    import json
    raw = json.loads(source.get("raw_json") or "{}") if isinstance(source.get("raw_json"), str) else {}
    sigs: list[dict] = []
    try:
        if source["platform"] == "youtube" and env().youtube_api_key:
            sigs = youtube_comment_signals(source["external_id"], float(source.get("duration_s") or 1e9))
        elif source["platform"] == "twitch" and raw.get("kind") == "vod" and env().twitch_client_id:
            sigs = twitch_vod_signals(raw)
    except Exception as ex:  # crowd data is a bonus – never block the pipeline
        log.warning("crowd signals for source %s failed: %s", source.get("id"), ex)
        return 0
    with db.tx() as conn:
        conn.execute("DELETE FROM crowd_signals WHERE source_id=?", (source["id"],))
        conn.executemany("INSERT INTO crowd_signals(source_id,t,dur,weight,kind,created_at) VALUES(?,?,?,?,?,?)",
                         [(source["id"], s["t"], s["dur"], s["weight"], s["kind"], db.now()) for s in sigs])
    if sigs:
        log.info("source %s: %d crowd signals", source["id"], len(sigs))
    return len(sigs)


def load(source_id: int) -> list[dict]:
    return [dict(r) for r in db.q("SELECT t, dur, weight, kind FROM crowd_signals WHERE source_id=?", (source_id,))]


def window_heat(signals: list[dict], s: float, e: float, lead: float = 3.0) -> float:
    """Sum of signal weight falling in [s-lead, e]. Comment timestamps usually point
    slightly *before/at* the moment, viewer clips cover an interval."""
    heat = 0.0
    for g in signals:
        if g["dur"] > 0:
            a, b = g["t"], g["t"] + g["dur"]
            inter = max(0.0, min(b, e) - max(a, s))
            heat += g["weight"] * inter / g["dur"]
        elif s - lead <= g["t"] <= e:
            heat += g["weight"]
    return heat


def window_scores(signals: list[dict], windows: list[tuple[float, float]]) -> list[float]:
    """0..1 per window (relative to the hottest window)."""
    if not signals:
        return [0.0] * len(windows)
    heats = [window_heat(signals, s, e) for s, e in windows]
    top = max(heats) or 1.0
    return [h / top for h in heats]
