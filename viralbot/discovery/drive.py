"""Google Drive intake: approved creators share a Drive folder with their raw
videos ("anyone with the link can view"). New videos in that folder are picked
up automatically – the cleanest, highest-quality and fully permitted source.

Uses the same Google API key as YouTube discovery (enable the *Google Drive API*
in that Google Cloud project).
"""
from __future__ import annotations

import re

import requests

from .. import db
from ..config import env
from ..logging_setup import get_logger

log = get_logger("discovery.drive")
API = "https://www.googleapis.com/drive/v3/files"


def folder_id(link: str) -> str | None:
    link = (link or "").strip()
    m = re.search(r"/folders/([A-Za-z0-9_-]{10,})", link) or re.search(r"[?&]id=([A-Za-z0-9_-]{10,})", link)
    if m:
        return m.group(1)
    return link if re.fullmatch(r"[A-Za-z0-9_-]{10,}", link) else None


def list_videos(fid: str) -> list[dict]:
    files, token = [], None
    for _ in range(10):
        params = {"q": f"'{fid}' in parents and trashed=false", "pageSize": 100, "key": env().youtube_api_key,
                  "fields": "nextPageToken,files(id,name,mimeType,size,createdTime,videoMediaMetadata(durationMillis))",
                  "supportsAllDrives": "true", "includeItemsFromAllDrives": "true"}
        if token:
            params["pageToken"] = token
        r = requests.get(API, params=params, timeout=30)
        if r.status_code >= 400:
            raise RuntimeError(f"Drive API {r.status_code}: {r.text[:200]}")
        data = r.json()
        files += [f for f in data.get("files", []) if (f.get("mimeType") or "").startswith("video/")]
        token = data.get("nextPageToken")
        if not token:
            break
    return files


def discover(settings: dict) -> list[dict]:
    from ..creators import default_attribution
    if not env().youtube_api_key:
        return []
    rows = [dict(r) for r in db.q("SELECT * FROM creators WHERE status='approved' AND drive_folder IS NOT NULL "
                                   "AND drive_folder != ''")]
    out = []
    for c in rows:
        fid = folder_id(c["drive_folder"])
        if not fid:
            log.warning("creator %s: invalid Drive folder link", c["name"] or c["channel_id"])
            continue
        try:
            files = list_videos(fid)
        except Exception as ex:
            log.warning("Drive folder of %s not readable (shared as 'anyone with the link'? Drive API enabled?): %s",
                        c["name"] or c["channel_id"], ex)
            continue
        for f in files:
            dur = (f.get("videoMediaMetadata") or {}).get("durationMillis")
            out.append({
                "platform": "drive", "external_id": f["id"], "url": f"{API}/{f['id']}?alt=media",
                "title": f["name"].rsplit(".", 1)[0], "channel_id": c["channel_id"],
                "channel_title": c["name"] or c["channel_id"], "published_at": f.get("createdTime"),
                "duration_s": float(dur) / 1000 if dur else None, "topic": f"creator:{c['name'] or c['channel_id']}",
                "rights_status": "authorized", "rights_evidence": f"Creator permission: {c['evidence']} (shared Drive folder)",
                "attribution": default_attribution(c), "raw": {"drive_folder": fid, "creator_id": c["id"]},
            })
    return out
