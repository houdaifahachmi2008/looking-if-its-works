"""Self-improvement: learn which clip features perform best on *your* channel.

For every uploaded clip with analytics we compute a performance z-score
(log views blended with retention), then for each feature value (topic,
length bucket, layout, zoom style, hook type, platform) a shrunken mean:

    mean_z = sum(z) / (n + K)          (K = 3: few samples → close to 0)
    adjustment = clip(mean_z * 6, ±max_adjustment)

Adjustments are added to new candidates' viral scores, re-order discovery
topics, and steer the preferred clip length / layout.
"""
from __future__ import annotations

import math

import numpy as np

from .. import db

K = 3.0
FEATURES = ("topic", "length", "layout", "zoom", "hook_type", "platform")


def length_bucket(seconds: float) -> str:
    lo = int(max(20, min(40, (seconds // 5) * 5)))
    return f"{lo}-{lo + 5}s"


def clip_features(clip: dict, platform: str = "") -> dict:
    return {"topic": clip.get("topic") or "", "length": length_bucket(clip.get("duration_s") or 30),
            "layout": clip.get("layout") or "", "zoom": clip.get("zoom_style") or "",
            "hook_type": clip.get("hook_type") or "", "platform": platform}


def feature_adjustment(feature: str, value: str) -> float:
    try:
        row = db.q1("SELECT adjustment FROM learning_weights WHERE feature=? AND value=?", (feature, value))
    except Exception:
        return 0.0
    return float(row["adjustment"]) if row else 0.0


def candidate_adjustment(features: dict) -> float:
    adj = [feature_adjustment(f, v) for f, v in features.items() if v]
    adj = [a for a in adj if a != 0]
    if not adj:
        return 0.0
    cap = float(db.get_setting("learning_max_adjustment") or 8)
    return float(np.clip(sum(adj) / math.sqrt(len(adj)), -cap, cap))


def preferred(feature: str) -> str | None:
    row = db.q1("SELECT value FROM learning_weights WHERE feature=? AND n>=2 ORDER BY adjustment DESC LIMIT 1", (feature,))
    return row["value"] if row and feature_adjustment(feature, row["value"]) > 0 else None


def performance_rows() -> list[dict]:
    """Latest analytics per uploaded clip joined with clip features."""
    return [dict(r) for r in db.q("""
        SELECT c.*, s.platform, a.views, a.likes, a.comments, a.avg_view_pct, a.avg_view_duration_s,
               a.subscribers_gained, u.youtube_video_id, u.uploaded_at
        FROM uploads u JOIN clips c ON c.id=u.clip_id JOIN sources s ON s.id=c.source_id
        JOIN analytics a ON a.id = (SELECT id FROM analytics WHERE upload_id=u.id ORDER BY id DESC LIMIT 1)
        WHERE u.status='uploaded'""")]


def recompute() -> dict:
    rows = performance_rows()
    min_n = int(db.get_setting("learning_min_samples") or 5)
    cap = float(db.get_setting("learning_max_adjustment") or 8)
    if len(rows) < min_n:
        return {"samples": len(rows), "updated": 0, "note": f"need ≥{min_n} clips with analytics"}
    perf = []
    for r in rows:
        v = math.log1p(r.get("views") or 0)
        ret = (r.get("avg_view_pct") or 0) / 100
        eng = ((r.get("likes") or 0) + 2 * (r.get("comments") or 0)) / max(1, r.get("views") or 0)
        perf.append(v + 2.0 * ret + 10 * eng)
    perf = np.array(perf)
    z = (perf - perf.mean()) / (perf.std() + 1e-9)
    acc: dict[tuple[str, str], list[float]] = {}
    for r, zi in zip(rows, z):
        for f, v in clip_features(r, r.get("platform", "")).items():
            if v:
                acc.setdefault((f, v), []).append(float(zi))
    with db.tx() as conn:
        conn.execute("DELETE FROM learning_weights")
        for (f, v), zs in acc.items():
            mean_shrunk = sum(zs) / (len(zs) + K)
            conn.execute("INSERT INTO learning_weights(feature,value,n,mean_perf,adjustment,updated_at) VALUES(?,?,?,?,?,?)",
                         (f, v, len(zs), float(np.mean(zs)), float(np.clip(mean_shrunk * 6, -cap, cap)), db.now()))
    return {"samples": len(rows), "updated": len(acc)}


def preferred_length_target(default: float) -> float:
    """Middle of the best-performing length bucket, else ``default``."""
    best = preferred("length")
    if not best:
        return float(default)
    lo = float(best.split("-")[0])
    return lo + 2.5
