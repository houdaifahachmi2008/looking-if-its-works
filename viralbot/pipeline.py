"""Pipeline orchestrator. Each stage moves rows from one status to the next.

Stages are idempotent and claim rows with a lease, so the bot can be killed
at any time and resumes where it left off (stale leases are released).
"""
from __future__ import annotations

import json
import time
import traceback
from datetime import datetime, timedelta, timezone

from . import db, metadata, quality
from .analysis.scorer import analyze_source
from .editing.renderer import make_thumbnail, render_clip
from .importer import ImportBlocked, import_source
from .logging_setup import get_logger
from .rights import is_processable
from .transcription import load_transcript, transcribe_source

log = get_logger("pipeline")
MAX_ATTEMPTS = 3
LEASE_MINUTES = 90
_last_auth_warning = 0.0
STALE = {"sources": {"importing": "queued", "transcribing": "imported", "analyzing": "transcribed"},
         "clips": {"rendering": "candidate"}}


def release_stale_leases() -> int:
    cutoff = (datetime.now(timezone.utc) - timedelta(minutes=LEASE_MINUTES)).replace(microsecond=0).isoformat()
    n = 0
    for table, mapping in STALE.items():
        for busy, back in mapping.items():
            cur = db.connect().execute(
                f"UPDATE {table} SET status=?, lease_at=NULL WHERE status=? AND (lease_at IS NULL OR lease_at < ?)",
                (back, busy, cutoff))
            n += cur.rowcount
    if n:
        log.warning("released %d stale leases (previous run interrupted)", n)
    return n


def _claim(table: str, row_id: int, from_status: str, to_status: str) -> bool:
    cur = db.connect().execute(f"UPDATE {table} SET status=?, lease_at=? WHERE id=? AND status=?",
                               (to_status, db.now(), row_id, from_status))
    return cur.rowcount == 1


def _fail(table: str, row: dict, retry_status: str, ex: Exception, stage: str) -> None:
    attempts = (row.get("attempts") or 0) + 1
    final = attempts >= MAX_ATTEMPTS or isinstance(ex, ImportBlocked)
    status = ("skipped" if isinstance(ex, ImportBlocked) else "error") if final else retry_status
    db.update(table, row["id"], status=status, attempts=attempts, error=f"{stage}: {ex}"[:2000], lease_at=None)
    log.error("%s %s failed at %s (attempt %d): %s", table[:-1], row["id"], stage, attempts, ex)
    log.debug(traceback.format_exc())


def stage_import(limit: int = 5) -> int:
    rows = [dict(r) for r in db.q("SELECT * FROM sources WHERE status='queued' ORDER BY discovery_score DESC LIMIT ?", (limit,))]
    done = 0
    for s in rows:
        if not is_processable(s["rights_status"]):
            db.update("sources", s["id"], status="lead")
            continue
        if not _claim("sources", s["id"], "queued", "importing"):
            continue
        try:
            import_source(s)
            db.update("sources", s["id"], status="imported", lease_at=None, error=None)
            done += 1
        except Exception as ex:
            _fail("sources", s, "queued", ex, "import")
    return done


def stage_transcribe(limit: int = 3) -> int:
    rows = [dict(r) for r in db.q("SELECT * FROM sources WHERE status='imported' LIMIT ?", (limit,))]
    done = 0
    for s in rows:
        if not _claim("sources", s["id"], "imported", "transcribing"):
            continue
        try:
            transcribe_source(s)
            db.update("sources", s["id"], status="transcribed", lease_at=None, error=None)
            done += 1
        except Exception as ex:
            _fail("sources", s, "imported", ex, "transcribe")
    return done


def stage_analyze(settings: dict, limit: int = 5) -> int:
    rows = [dict(r) for r in db.q("SELECT * FROM sources WHERE status='transcribed' LIMIT ?", (limit,))]
    made = 0
    for s in rows:
        if not _claim("sources", s["id"], "transcribed", "analyzing"):
            continue
        try:
            tr = load_transcript(s["id"]) or {"words": [], "segments": []}
            made += len(analyze_source(s, tr, settings))
            db.update("sources", s["id"], status="analyzed", lease_at=None, error=None)
        except Exception as ex:
            _fail("sources", s, "transcribed", ex, "analyze")
    return made


def stage_render(settings: dict, limit: int = 3) -> int:
    rows = [dict(r) for r in db.q("SELECT * FROM clips WHERE status='candidate' ORDER BY adjusted_score DESC LIMIT ?", (limit,))]
    done = 0
    for c in rows:
        if not _claim("clips", c["id"], "candidate", "rendering"):
            continue
        try:
            source = dict(db.q1("SELECT * FROM sources WHERE id=?", (c["source_id"],)))
            if not is_processable(source["rights_status"]):
                db.update("clips", c["id"], status="rejected", error="rights revoked")
                continue
            tr = load_transcript(source["id"]) or {"words": [], "segments": []}
            res = render_clip(c, source, tr, settings)
            thumb = make_thumbnail(res.path, res.path.with_name("thumbnail.jpg"))
            db.update("clips", c["id"], status="rendered", file_path=str(res.path), thumbnail_path=str(thumb),
                      layout=res.layout, zoom_style=res.zoom_style, duration_s=round(res.duration, 2), lease_at=None,
                      error=None)
            metadata.apply(c["id"])
            quality.apply(c["id"], settings)
            done += 1
        except Exception as ex:
            _fail("clips", c, "candidate", ex, "render")
    return done


def stage_upload(settings: dict, limit: int = 1) -> int:
    from .publishing.uploader import UploadBlocked, upload_clip, uploads_today
    from .publishing.youtube_auth import is_authorized

    if uploads_today() >= int(settings["max_daily_uploads"]):
        return 0
    rows = [dict(r) for r in db.q("SELECT * FROM clips WHERE status='approved' ORDER BY adjusted_score DESC LIMIT ?", (limit,))]
    if not rows:
        return 0
    if not is_authorized():
        global _last_auth_warning
        if time.time() - _last_auth_warning > 3600:  # don't flood the event log every tick
            log.warning("approved clips waiting but YouTube is not authorized (python -m viralbot auth-youtube)")
            _last_auth_warning = time.time()
        return 0
    n = 0
    for c in rows:
        try:
            upload_clip(c["id"], settings)
            n += 1
        except UploadBlocked as ex:
            log.info("upload of clip %s skipped: %s", c["id"], ex)
        except Exception as ex:
            log.error("upload of clip %s failed: %s", c["id"], ex)
            if (c.get("attempts") or 0) + 1 < MAX_ATTEMPTS:
                db.update("clips", c["id"], status="approved")
    return n


def requeue_failed_uploads() -> None:
    db.connect().execute(f"UPDATE clips SET status='approved' WHERE status='upload_failed' AND attempts < {MAX_ATTEMPTS}")


def process_once(settings: dict | None = None, upload: bool = False) -> dict:
    settings = settings or db.all_settings()
    release_stale_leases()
    out = {"imported": stage_import(), "transcribed": stage_transcribe(), "clips_created": stage_analyze(settings),
           "rendered": stage_render(settings)}
    if upload:
        requeue_failed_uploads()
        out["uploaded"] = stage_upload(settings)
    return out


def counts() -> dict:
    def c(sql, *p):
        return int(db.scalar(sql, p, 0))
    return {
        "discovered": c("SELECT COUNT(*) FROM sources"),
        "processable": c("SELECT COUNT(*) FROM sources WHERE rights_status IN ('owned','licensed','creative_commons','authorized')"),
        "analyzed": c("SELECT COUNT(*) FROM sources WHERE status='analyzed'"),
        "clips": c("SELECT COUNT(*) FROM clips WHERE file_path IS NOT NULL"),
        "pending_approval": c("SELECT COUNT(*) FROM clips WHERE status='pending_approval'"),
        "approved": c("SELECT COUNT(*) FROM clips WHERE status='approved'"),
        "uploaded": c("SELECT COUNT(*) FROM uploads WHERE status='uploaded'"),
        "errors": c("SELECT COUNT(*) FROM sources WHERE status='error'") + c("SELECT COUNT(*) FROM clips WHERE status IN ('error','qc_failed','upload_failed')"),
    }


def set_clip_decision(clip_id: int, approve: bool, reason: str = "", commentary: str | None = None) -> None:
    clip = db.q1("SELECT status, risk_level FROM clips WHERE id=?", (clip_id,))
    if clip is None:
        raise KeyError(clip_id)
    if approve:
        if clip["status"] not in {"pending_approval", "rejected", "qc_failed"}:
            raise ValueError(f"clip is '{clip['status']}'")
        if clip["risk_level"] == "high":
            raise ValueError("high copyright risk – fix the source's rights status first")
        db.update("clips", clip_id, status="approved", error=None)
    else:
        db.update("clips", clip_id, status="rejected", error=reason or "rejected by reviewer")
    if commentary is not None:
        db.update("clips", clip_id, commentary=commentary)
    db.log_event("INFO", "review", json.dumps({"clip": clip_id, "approve": approve, "reason": reason}))
