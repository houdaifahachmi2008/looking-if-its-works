"""Upsert discovered items into the ``sources`` table."""
from __future__ import annotations

import json

from .. import db
from ..analysis.learning import feature_adjustment
from ..rights import is_processable
from .ranking import discovery_score, snapshot_and_growth


def upsert_source(item: dict) -> tuple[int, bool]:
    """Insert or refresh a discovered item. Returns (id, is_new).

    ``item`` keys: platform, external_id, url, title, channel_id, channel_title,
    published_at, duration_s, views, likes, comments, topic, rights_status,
    license, attribution, raw.
    """
    existing = db.q1("SELECT id, rights_status, status FROM sources WHERE platform=? AND external_id=?",
                     (item["platform"], item["external_id"]))
    views, likes, comments = int(item.get("views") or 0), int(item.get("likes") or 0), int(item.get("comments") or 0)
    topic_adj = feature_adjustment("topic", item.get("topic") or "")
    if existing:
        sid = existing["id"]
        growth = snapshot_and_growth(sid, views, likes, comments)
        score, vph, er = discovery_score(views, likes, comments, item.get("published_at"), growth, topic_adj)
        fields = dict(views=views, likes=likes, comments=comments, views_per_hour=vph, engagement_rate=er,
                      discovery_score=score)
        if growth is not None:
            fields["growth_per_hour"] = round(growth, 2)
        # never downgrade a manually set rights status
        if existing["rights_status"] == "unknown" and item.get("rights_status", "unknown") != "unknown":
            fields["rights_status"] = item["rights_status"]
            fields["license"] = item.get("license")
            fields["attribution"] = item.get("attribution")
            if existing["status"] in {"lead", "discovered"} and is_processable(item["rights_status"]):
                fields["status"] = "queued"
        db.update("sources", sid, **fields)
        return sid, False

    score, vph, er = discovery_score(views, likes, comments, item.get("published_at"), None, topic_adj)
    rights_status = item.get("rights_status") or "unknown"
    status = "queued" if is_processable(rights_status) else "lead"
    sid = db.ex(
        """INSERT INTO sources(platform, external_id, url, title, channel_id, channel_title, published_at,
               duration_s, views, likes, comments, engagement_rate, views_per_hour, discovery_score, topic,
               rights_status, license, attribution, rights_evidence, status, local_path, raw_json, created_at, updated_at)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (item["platform"], item["external_id"], item.get("url"), item.get("title"), item.get("channel_id"),
         item.get("channel_title"), item.get("published_at"), item.get("duration_s"), views, likes, comments,
         er, vph, score, item.get("topic"), rights_status, item.get("license"), item.get("attribution"),
         item.get("rights_evidence"), status, item.get("local_path"),
         json.dumps(item.get("raw") or {})[:20000], db.now(), db.now()),
    )
    snapshot_and_growth(sid, views, likes, comments)
    return sid, True
