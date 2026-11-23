"""Discovery engine: finds candidate source videos and ranks them."""
from __future__ import annotations

from .. import db
from ..logging_setup import get_logger
from . import drive, local_inbox, twitch, youtube
from .store import upsert_source

log = get_logger("discovery")
PROVIDERS = {"local_inbox": local_inbox.discover, "drive": drive.discover, "youtube": youtube.discover,
             "twitch": twitch.discover}


def run_discovery(settings: dict | None = None) -> dict:
    settings = settings or db.all_settings()
    enabled = settings.get("sources_enabled", {})
    stats = {"found": 0, "new": 0, "processable_new": 0}
    for name, fn in PROVIDERS.items():
        if not enabled.get(name, True):
            continue
        try:
            items = fn(settings)
        except Exception as ex:  # one provider failing must not stop the others
            log.exception("provider %s failed: %s", name, ex)
            continue
        for it in items:
            sid, is_new = upsert_source(it)
            stats["found"] += 1
            if is_new:
                stats["new"] += 1
                if it.get("rights_status", "unknown") in {"owned", "licensed", "creative_commons", "authorized"}:
                    stats["processable_new"] += 1
        log.info("%s: %d items", name, len(items))
    db.set_setting("last_discovery_at", db.now())
    return stats
