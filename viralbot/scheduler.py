"""Continuous worker loop with heartbeat, start/stop via DB flag and auto-recovery."""
from __future__ import annotations

import os
import signal
import time
from datetime import datetime, timedelta, timezone

from . import analytics, db, pipeline
from .discovery import run_discovery
from .logging_setup import get_logger

log = get_logger("scheduler")
_stop = False


def _handle(sig, frame):  # pragma: no cover
    global _stop
    _stop = True
    log.info("signal %s received – finishing current step", sig)


def _due(key: str, hours: float) -> bool:
    last = db.get_setting(key)
    if not last:
        return True
    try:
        return datetime.now(timezone.utc) - datetime.fromisoformat(last) >= timedelta(hours=hours)
    except ValueError:
        return True


def heartbeat(state: str) -> None:
    db.set_setting("worker_heartbeat", {"ts": db.now(), "pid": os.getpid(), "state": state})


def worker_alive(max_age_s: int = 120) -> bool:
    hb = db.get_setting("worker_heartbeat")
    if not hb:
        return False
    try:
        return (datetime.now(timezone.utc) - datetime.fromisoformat(hb["ts"])).total_seconds() < max_age_s
    except (KeyError, ValueError, TypeError):
        return False


def _reload_env() -> None:
    """Pick up keys saved from the dashboard's Connections page without a restart."""
    try:
        from dotenv import load_dotenv
        from .config import PROJECT_ROOT, reset_env
        load_dotenv(PROJECT_ROOT / ".env", override=True)
        reset_env()
    except ImportError:  # pragma: no cover
        pass


def tick() -> dict:
    _reload_env()
    s = db.all_settings()
    out: dict = {}
    if _due("last_discovery_at", float(s["discovery_interval_hours"])):
        heartbeat("discovering")
        out["discovery"] = run_discovery(s)
    heartbeat("processing")
    out["process"] = pipeline.process_once(s, upload=False)
    heartbeat("uploading")
    pipeline.requeue_failed_uploads()
    out["uploaded"] = pipeline.stage_upload(s)
    from .publishing.youtube_auth import is_authorized
    if is_authorized():
        if _due("last_analytics_at", float(s["analytics_interval_hours"])):
            heartbeat("analytics")
            out["analytics"] = analytics.collect()
        try:
            from .publishing.uploader import reconcile_stuck_uploads
            reconcile_stuck_uploads()
        except Exception as ex:
            log.warning("upload reconciliation failed: %s", ex)
    return out


def run_forever(idle_sleep: int = 20, busy_sleep: int = 5, max_ticks: int | None = None) -> None:
    signal.signal(signal.SIGTERM, _handle)
    signal.signal(signal.SIGINT, _handle)
    db.init_db()
    log.info("worker started (pid %s)", os.getpid())
    backoff = 0
    ticks = 0
    while not _stop:
        if not db.get_setting("bot_enabled"):
            heartbeat("paused")
            time.sleep(idle_sleep)
            continue
        try:
            res = tick()
            backoff = 0
            busy = any(v for v in res.get("process", {}).values())
            log.info("tick: %s", res)
            heartbeat("idle")
            time.sleep(busy_sleep if busy else idle_sleep * 3)
        except Exception as ex:  # auto-recovery: log, back off, continue
            backoff = min(600, (backoff or 15) * 2)
            log.exception("tick failed: %s – retrying in %ss", ex, backoff)
            heartbeat("error")
            time.sleep(backoff)
        ticks += 1
        if max_ticks and ticks >= max_ticks:
            break
    heartbeat("stopped")
    log.info("worker stopped")
