"""Discovery ranking: which sources are gaining attention fastest?"""
from __future__ import annotations

import math
from datetime import datetime, timezone

from .. import db


def hours_since(iso: str | None) -> float:
    if not iso:
        return 24.0 * 30
    try:
        t = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return 24.0 * 30
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return max(1.0, (datetime.now(timezone.utc) - t).total_seconds() / 3600)


def engagement_rate(views: int, likes: int, comments: int, prior_views: int = 500) -> float:
    """(likes + 2·comments) / views, shrunk toward 0 for small samples so a video
    with 3 views and 1 like does not look like 33% engagement."""
    if not views:
        return 0.0
    return (likes + 2 * comments) / (views + prior_views)


def discovery_score(views: int, likes: int, comments: int, published_at: str | None,
                    growth_per_hour: float | None = None, topic_adjustment: float = 0.0) -> tuple[float, float, float]:
    """0-100 score prioritising fast-growing, high-engagement, recent content.

    Returns (score, views_per_hour, engagement_rate).
    """
    age_h = hours_since(published_at)
    vph = views / age_h
    er = engagement_rate(views, likes, comments)
    # velocity: 10 views/h ~ 17 pts, 1k/h ~ 50 pts, 100k/h ~ 83 pts (log scale), max 50
    velocity = min(50.0, 8.3 * math.log10(1 + vph))
    # engagement: 5% likes+comments/views is strong for long-form; cap at 25 pts
    engagement = min(25.0, er * 500)
    # recency: full points under 24h, decays over ~2 weeks
    recency = 15.0 * math.exp(-age_h / 240)
    # measured growth between our own snapshots (strongest "gaining attention" signal)
    growth = 0.0
    if growth_per_hour is not None and growth_per_hour > 0:
        growth = min(10.0, 2.5 * math.log10(1 + growth_per_hour))
    score = velocity + engagement + recency + growth + topic_adjustment
    return round(max(0.0, min(100.0, score)), 2), round(vph, 2), round(er, 4)


def snapshot_and_growth(source_id: int, views: int, likes: int, comments: int) -> float | None:
    """Store a metric snapshot and return view growth/hour vs the previous snapshot."""
    prev = db.q1("SELECT ts, views FROM metric_snapshots WHERE source_id=? ORDER BY id DESC LIMIT 1", (source_id,))
    db.ex("INSERT INTO metric_snapshots(source_id,ts,views,likes,comments) VALUES(?,?,?,?,?)",
          (source_id, db.now(), views, likes, comments))
    if prev is None:
        return None
    dt = hours_since(prev["ts"])
    if dt < 0.05:
        return None
    return max(0.0, (views - (prev["views"] or 0)) / dt)
