"""YouTube Shorts uploader (official Data API v3, resumable uploads).

Safety rails:
* rights re-checked right before upload; high-risk clips are never uploaded
* daily upload cap + quota guard (an upload costs ~1,600 quota units)
* default privacy private/unlisted; 'public' only if public_publishing_approved
* idempotency: one upload row per clip (UNIQUE), content-hash de-dup, and an
  invisible marker tag so a crash mid-upload can be reconciled without
  uploading twice
* exponential-backoff retries for transient HTTP/network errors
"""
from __future__ import annotations

import http.client
import json
import random
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .. import db
from ..config import env
from ..logging_setup import get_logger
from ..rights import is_processable
from .youtube_auth import youtube_client

log = get_logger("uploader")
UPLOAD_QUOTA = 1600
RETRIABLE_STATUS = {500, 502, 503, 504}
RETRIABLE_EXC = (http.client.NotConnected, http.client.IncompleteRead, http.client.ImproperConnectionState,
                 http.client.CannotSendRequest, http.client.CannotSendHeader, http.client.ResponseNotReady,
                 http.client.BadStatusLine, ConnectionError, TimeoutError, OSError)


class UploadBlocked(RuntimeError):
    pass


def marker(clip: dict) -> str:
    return f"vb{clip['id']}x{(clip.get('content_hash') or '')[:8]}"


def uploads_today() -> int:
    today = datetime.now(timezone.utc).date().isoformat()
    return int(db.scalar("SELECT COUNT(*) FROM uploads WHERE status='uploaded' AND substr(uploaded_at,1,10)=?", (today,)))


def resolve_privacy(settings: dict) -> str:
    p = settings.get("default_privacy", "private")
    if p == "public" and not settings.get("public_publishing_approved", False):
        return "private"
    return p if p in {"private", "unlisted", "public"} else "private"


def next_publish_slot(settings: dict) -> str | None:
    """Next free UTC slot for scheduled publishing (YouTube requires privacy=private + publishAt)."""
    if not settings.get("schedule_uploads") or settings.get("default_privacy") != "public" \
            or not settings.get("public_publishing_approved"):
        return None
    slots = sorted(settings.get("upload_slots_utc") or ["18:00"])
    taken = {r["publish_at"] for r in db.q("SELECT publish_at FROM uploads WHERE publish_at IS NOT NULL")}
    now = datetime.now(timezone.utc)
    for day in range(0, 30):
        d = (now + timedelta(days=day)).date()
        for s in slots:
            hh, mm = (int(x) for x in s.split(":"))
            t = datetime(d.year, d.month, d.day, hh, mm, tzinfo=timezone.utc)
            iso = t.strftime("%Y-%m-%dT%H:%M:%SZ")
            if t > now + timedelta(minutes=30) and iso not in taken:
                return iso
    return None


def build_body(clip: dict, settings: dict) -> tuple[dict, str | None]:
    title = (clip.get("title") or "Short").strip()
    if "#shorts" not in title.lower() and len(title) + 8 <= 100:
        title = f"{title} #Shorts"
    tags = [h.lstrip("#") for h in json.loads(clip.get("hashtags") or "[]")] + [marker(clip)]
    publish_at = next_publish_slot(settings)
    privacy = "private" if publish_at else resolve_privacy(settings)
    status = {"privacyStatus": privacy, "selfDeclaredMadeForKids": False, "embeddable": True,
              "license": "youtube"}
    if publish_at:
        status["publishAt"] = publish_at
    body = {
        "snippet": {"title": title[:100], "description": (clip.get("description") or "")[:5000],
                    "tags": tags[:30], "categoryId": clip.get("category_id") or settings.get("default_category_id", "24"),
                    "defaultLanguage": "en", "defaultAudioLanguage": "en"},
        "status": status,
    }
    return body, publish_at


def _execute_resumable(request) -> dict:
    from googleapiclient.errors import HttpError

    response, retry = None, 0
    while response is None:
        try:
            _, response = request.next_chunk()
        except HttpError as e:
            if e.resp.status in RETRIABLE_STATUS:
                err = e
            else:
                raise
        except RETRIABLE_EXC as e:
            err = e
        else:
            continue
        retry += 1
        if retry > 8:
            raise RuntimeError(f"upload failed after retries: {err}")
        sleep = min(64, 2 ** retry) * random.random() + 1
        log.warning("transient upload error (%s), retry %d in %.1fs", err, retry, sleep)
        time.sleep(sleep)
    return response


def upload_clip(clip_id: int, settings: dict) -> str:
    from googleapiclient.http import MediaFileUpload

    clip = dict(db.q1("SELECT * FROM clips WHERE id=?", (clip_id,)))
    source = dict(db.q1("SELECT * FROM sources WHERE id=?", (clip["source_id"],)))
    if clip["status"] != "approved":
        raise UploadBlocked(f"clip {clip_id} is '{clip['status']}', not approved")
    if not is_processable(source["rights_status"]) or clip.get("risk_level") == "high":
        raise UploadBlocked("rights not cleared / high copyright risk")
    if db.q1("SELECT 1 FROM uploads WHERE clip_id=? AND status='uploaded'", (clip_id,)) or \
            db.q1("SELECT 1 FROM uploads WHERE content_hash=? AND status='uploaded'", (clip.get("content_hash"),)):
        db.update("clips", clip_id, status="uploaded")
        raise UploadBlocked("already uploaded (duplicate)")
    if uploads_today() >= int(settings["max_daily_uploads"]):
        raise UploadBlocked("daily upload limit reached")
    if db.youtube_quota_used_today() + UPLOAD_QUOTA > env().youtube_daily_quota * 0.95:
        raise UploadBlocked("YouTube API quota budget exhausted for today")
    path = Path(clip["file_path"])
    if not path.exists():
        raise UploadBlocked(f"file missing: {path}")

    body, publish_at = build_body(clip, settings)
    db.update("clips", clip_id, status="uploading", lease_at=db.now())
    yt = youtube_client()
    media = MediaFileUpload(str(path), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True)
    request = yt.videos().insert(part="snippet,status", body=body, media_body=media)
    db.record_cost("youtube", "data-api-v3", "upload", UPLOAD_QUOTA, "quota", 0.0)
    try:
        response = _execute_resumable(request)
    except Exception as ex:
        db.update("clips", clip_id, status="upload_failed", error=str(ex)[:1000], attempts=(clip["attempts"] or 0) + 1)
        db.ex("INSERT INTO uploads(clip_id,status,attempts,error,content_hash) VALUES(?,?,1,?,?) "
              "ON CONFLICT(clip_id) DO UPDATE SET attempts=attempts+1, error=excluded.error, status='failed'",
              (clip_id, "failed", str(ex)[:1000], clip.get("content_hash")))
        raise
    vid = response["id"]
    _record_success(clip_id, vid, body["status"]["privacyStatus"], publish_at, clip.get("content_hash"))

    if settings.get("post_credit_comment"):
        post_credit_comment(yt, vid, source, body["status"]["privacyStatus"], publish_at, settings)

    if settings.get("set_custom_thumbnail") and clip.get("thumbnail_path") and Path(clip["thumbnail_path"]).exists():
        try:
            yt.thumbnails().set(videoId=vid, media_body=MediaFileUpload(clip["thumbnail_path"])).execute()
            db.record_cost("youtube", "data-api-v3", "thumbnail", 50, "quota", 0.0)
        except Exception as ex:  # requires a verified channel; Shorts often ignore custom thumbnails
            log.warning("thumbnail not set for %s: %s", vid, ex)
    log.info("clip %s uploaded → https://youtube.com/shorts/%s (%s%s)", clip_id, vid, body["status"]["privacyStatus"],
             f", publishAt {publish_at}" if publish_at else "")
    return vid


def credit_comment_text(source: dict, settings: dict) -> str:
    from ..rights import attribution_line
    credit = attribution_line(source) if source.get("rights_status") != "owned" else ""
    tmpl = settings.get("credit_comment_text") or "{credit}"
    lines = [ln for ln in tmpl.split("\n") if credit or "{credit}" not in ln]
    return "\n".join(lines).format(credit=credit).strip()


def post_credit_comment(yt, vid: str, source: dict, privacy: str, publish_at: str | None, settings: dict) -> bool:
    """Comment under the new Short crediting the creator. YouTube's API cannot *pin* comments –
    pin it yourself in YouTube Studio if you like. Private/scheduled videos can't receive comments yet."""
    from .youtube_auth import COMMENT_SCOPE, has_scope
    if privacy == "private" or publish_at:
        log.info("credit comment skipped for %s (video is private/scheduled)", vid)
        return False
    if not has_scope(COMMENT_SCOPE):
        log.warning("credit comment needs one new login (Connections → log out → log in with Google)")
        return False
    text = credit_comment_text(source, settings)
    if not text:
        return False
    try:
        yt.commentThreads().insert(part="snippet", body={"snippet": {"videoId": vid, "topLevelComment": {
            "snippet": {"textOriginal": text[:9000]}}}}).execute()
        db.record_cost("youtube", "data-api-v3", "credit_comment", 50, "quota", 0.0)
        return True
    except Exception as ex:
        log.warning("credit comment on %s failed: %s", vid, ex)
        return False


def _record_success(clip_id: int, vid: str, privacy: str, publish_at: str | None, content_hash: str | None) -> None:
    db.ex("INSERT INTO uploads(clip_id,youtube_video_id,privacy,publish_at,status,attempts,content_hash,uploaded_at) "
          "VALUES(?,?,?,?,'uploaded',1,?,?) ON CONFLICT(clip_id) DO UPDATE SET youtube_video_id=excluded.youtube_video_id,"
          " privacy=excluded.privacy, publish_at=excluded.publish_at, status='uploaded', error=NULL,"
          " uploaded_at=excluded.uploaded_at", (clip_id, vid, privacy, publish_at, content_hash, db.now()))
    db.update("clips", clip_id, status="uploaded", error=None)


def reconcile_stuck_uploads(max_age_minutes: int = 90) -> int:
    """Clips left in 'uploading' (crash mid-upload): look for our marker tag on the
    channel's recent uploads; record it if found, otherwise re-queue."""
    stuck = [dict(r) for r in db.q("SELECT * FROM clips WHERE status='uploading'")]
    stuck = [c for c in stuck if not c.get("lease_at") or
             datetime.fromisoformat(c["lease_at"]) < datetime.now(timezone.utc) - timedelta(minutes=max_age_minutes)]
    if not stuck:
        return 0
    yt = youtube_client()
    ch = yt.channels().list(part="contentDetails", mine=True).execute()
    playlist = ch["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
    items = yt.playlistItems().list(part="contentDetails", playlistId=playlist, maxResults=50).execute()
    ids = [i["contentDetails"]["videoId"] for i in items.get("items", [])]
    found = {}
    if ids:
        for v in yt.videos().list(part="snippet,status", id=",".join(ids)).execute().get("items", []):
            for t in v["snippet"].get("tags", []):
                found[t] = (v["id"], v["status"].get("privacyStatus"))
    db.record_cost("youtube", "data-api-v3", "reconcile", 3, "quota", 0.0)
    for c in stuck:
        m = marker(c)
        if m in found:
            _record_success(c["id"], found[m][0], found[m][1], None, c.get("content_hash"))
        else:
            db.update("clips", c["id"], status="approved")
    return len(stuck)
