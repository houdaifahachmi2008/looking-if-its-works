"""Local inbox: drop your own / licensed videos into ``data/inbox``.

This is the most reliable, zero-cost and lowest-risk source: podcasts you
recorded, streams you own (Twitch VOD exports, OBS recordings), or footage you
licensed. Optional sidecar files next to a video:

* ``<name>.rights.json``  – {"rights_status": "owned"|"licensed"|"authorized"|"creative_commons",
                             "evidence": "...", "attribution": "...", "title": "...", "topic": "..."}
* ``<name>.srt`` / ``.vtt`` / ``.json`` – existing captions (skips Whisper).

Files without a rights sidecar default to ``INBOX_DEFAULT_RIGHTS`` (env, default "owned")
because the inbox is meant for your own content.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from ..config import env
from ..logging_setup import get_logger

log = get_logger("discovery.inbox")
VIDEO_EXT = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}


def file_id(p: Path) -> str:
    st = p.stat()
    return hashlib.sha1(f"{p.resolve()}|{st.st_size}".encode()).hexdigest()[:16]


def discover(settings: dict) -> list[dict]:
    inbox = env().dir("inbox")
    default_rights = os.getenv("INBOX_DEFAULT_RIGHTS", "owned")
    out = []
    for p in sorted(inbox.rglob("*")):
        if p.suffix.lower() not in VIDEO_EXT or not p.is_file():
            continue
        meta = {}
        side = p.with_suffix(".rights.json")
        if side.exists():
            try:
                meta = json.loads(side.read_text())
            except json.JSONDecodeError:
                log.warning("invalid rights sidecar %s", side)
        out.append({
            "platform": "local",
            "external_id": file_id(p),
            "url": p.resolve().as_uri(),
            "title": meta.get("title") or p.stem.replace("_", " ").replace("-", " "),
            "channel_title": meta.get("channel_title", "own content"),
            "published_at": None,
            "topic": meta.get("topic", "own content"),
            "rights_status": meta.get("rights_status", default_rights),
            "rights_evidence": meta.get("evidence", f"Placed in local inbox (default rights: {default_rights})"),
            "attribution": meta.get("attribution"),
            "license": meta.get("license"),
            "local_path": str(p.resolve()),
        })
    return out
