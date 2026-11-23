"""Performance tracking for uploaded Shorts + learning refresh.

* YouTube Data API ``videos.list(statistics)`` – views/likes/comments (1 unit per 50 videos)
* YouTube Analytics API v2 – average view duration, average % viewed,
  subscribers gained (needs the yt-analytics.readonly scope; failures are
  tolerated so basic stats still work).
"""
from __future__ import annotations

from datetime import date, timedelta

from . import db
from .analysis import learning
from .logging_setup import get_logger
from .publishing.youtube_auth import analytics_client, youtube_client

log = get_logger("analytics")


def collect() -> dict:
    ups = [dict(r) for r in db.q("SELECT id, youtube_video_id, uploaded_at FROM uploads WHERE status='uploaded' "
                                   "AND youtube_video_id IS NOT NULL")]
    if not ups:
        return {"videos": 0}
    by_vid = {u["youtube_video_id"]: u for u in ups}
    stats: dict[str, dict] = {}
    yt = youtube_client()
    vids = list(by_vid)
    for i in range(0, len(vids), 50):
        res = yt.videos().list(part="statistics", id=",".join(vids[i:i + 50])).execute()
        db.record_cost("youtube", "data-api-v3", "analytics:videos", 1, "quota", 0.0)
        for v in res.get("items", []):
            st = v.get("statistics", {})
            stats[v["id"]] = {"views": int(st.get("viewCount", 0)), "likes": int(st.get("likeCount", 0)),
                              "comments": int(st.get("commentCount", 0))}

    deep: dict[str, dict] = {}
    try:
        ya = analytics_client()
        start = min(u["uploaded_at"][:10] for u in ups)
        for i in range(0, len(vids), 200):
            chunk = vids[i:i + 200]
            rep = ya.reports().query(
                ids="channel==MINE", startDate=start, endDate=(date.today() + timedelta(days=1)).isoformat(),
                metrics="views,averageViewDuration,averageViewPercentage,subscribersGained",
                dimensions="video", filters="video==" + ",".join(chunk), maxResults=200).execute()
            cols = [c["name"] for c in rep.get("columnHeaders", [])]
            for row in rep.get("rows", []) or []:
                d = dict(zip(cols, row))
                deep[d["video"]] = d
    except Exception as ex:
        log.info("YouTube Analytics API unavailable (%s) – using basic statistics only", str(ex)[:200])

    for vid, u in by_vid.items():
        s = stats.get(vid)
        if s is None:
            continue
        d = deep.get(vid, {})
        db.ex("INSERT INTO analytics(upload_id,ts,views,likes,comments,subscribers_gained,avg_view_duration_s,avg_view_pct)"
              " VALUES(?,?,?,?,?,?,?,?)",
              (u["id"], db.now(), s["views"], s["likes"], s["comments"], d.get("subscribersGained"),
               d.get("averageViewDuration"), d.get("averageViewPercentage")))
    learn = learning.recompute()
    db.set_setting("last_analytics_at", db.now())
    log.info("analytics: %d videos updated; learning: %s", len(stats), learn)
    return {"videos": len(stats), "learning": learn}
