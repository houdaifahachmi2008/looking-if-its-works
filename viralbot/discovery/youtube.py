"""YouTube discovery via the official YouTube Data API v3 (API key, read-only).

Quota-aware: search.list costs 100 units, videos.list / playlistItems.list cost 1.
Default project quota is 10,000 units/day and an upload costs ~1,600, so
discovery is capped to leave room for uploads (see ``QuotaGuard``).
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

import requests

from .. import db
from ..config import env
from ..logging_setup import get_logger

log = get_logger("discovery.youtube")
API = "https://www.googleapis.com/youtube/v3"
UPLOAD_RESERVE_UNITS = 1700 * 3  # keep room for ~3 uploads/day


class QuotaExceeded(RuntimeError):
    pass


class QuotaGuard:
    COST = {"search": 100, "videos": 1, "playlistItems": 1, "channels": 1}

    @staticmethod
    def spend(endpoint: str, reserve: int = UPLOAD_RESERVE_UNITS) -> None:
        cost = QuotaGuard.COST.get(endpoint, 1)
        budget = env().youtube_daily_quota * 0.9 - reserve
        if db.youtube_quota_used_today() + cost > budget:
            raise QuotaExceeded(f"YouTube quota budget reached (reserve {reserve} units for uploads)")
        db.record_cost("youtube", "data-api-v3", f"discovery:{endpoint}", cost, "quota", 0.0)


def parse_iso_duration(s: str | None) -> float:
    if not s:
        return 0.0
    m = re.fullmatch(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", s)
    if not m:
        return 0.0
    d, h, mi, se = (int(x or 0) for x in m.groups())
    return d * 86400 + h * 3600 + mi * 60 + se


def _get(endpoint: str, params: dict) -> dict:
    QuotaGuard.spend(endpoint)
    params = {**params, "key": env().youtube_api_key}
    r = requests.get(f"{API}/{endpoint}", params=params, timeout=30)
    if r.status_code == 403 and "quota" in r.text.lower():
        raise QuotaExceeded(r.text[:300])
    r.raise_for_status()
    return r.json()


def _rights_for(item: dict, cc_license: bool) -> dict:
    e = env()
    ch = item.get("channel_id") or ""
    if ch and ch in e.owned_youtube_channels:
        return {"rights_status": "owned", "rights_evidence": "Channel listed in OWNED_YOUTUBE_CHANNELS"}
    from ..creators import rights_for_item
    approved = rights_for_item("youtube", ch)
    if approved:
        return approved
    if ch and ch in e.authorized_youtube_channels:
        return {"rights_status": "authorized",
                "rights_evidence": "Channel listed in AUTHORIZED_YOUTUBE_CHANNELS (keep written permission on file)"}
    if cc_license:
        return {"rights_status": "creative_commons", "license": "CC BY 3.0 (YouTube Creative Commons)",
                "attribution": f"Source: \"{item['title']}\" by {item['channel_title']} {item['url']} "
                               f"(CC BY 3.0 – creativecommons.org/licenses/by/3.0)",
                "rights_evidence": "YouTube API status.license == creativeCommon"}
    return {"rights_status": "unknown"}


def video_details(ids: list[str]) -> list[dict]:
    out = []
    for i in range(0, len(ids), 50):
        data = _get("videos", {"part": "snippet,statistics,contentDetails,status", "id": ",".join(ids[i:i + 50])})
        for v in data.get("items", []):
            sn, st, cd, status = v.get("snippet", {}), v.get("statistics", {}), v.get("contentDetails", {}), v.get("status", {})
            item = {
                "platform": "youtube",
                "external_id": v["id"],
                "url": f"https://www.youtube.com/watch?v={v['id']}",
                "title": sn.get("title", ""),
                "channel_id": sn.get("channelId"),
                "channel_title": sn.get("channelTitle", ""),
                "published_at": sn.get("publishedAt"),
                "duration_s": parse_iso_duration(cd.get("duration")),
                "views": int(st.get("viewCount", 0)),
                "likes": int(st.get("likeCount", 0)),
                "comments": int(st.get("commentCount", 0)),
                "raw": {"tags": sn.get("tags", [])[:15], "categoryId": sn.get("categoryId"),
                        "license": status.get("license"), "definition": cd.get("definition"),
                        "liveBroadcastContent": sn.get("liveBroadcastContent")},
            }
            item.update(_rights_for(item, status.get("license") == "creativeCommon"))
            out.append(item)
    return out


def search_topic(topic: str, lookback_days: int, max_results: int, cc_only: bool, min_views: int = 0) -> list[dict]:
    after = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%dT%H:%M:%SZ")
    params = {"part": "id", "q": topic, "type": "video", "order": "viewCount", "publishedAfter": after,
              "maxResults": min(50, max_results), "relevanceLanguage": "en", "safeSearch": "moderate",
              "videoDuration": "medium"}  # 4–20 min: enough material, cheap to process
    if cc_only:
        params["videoLicense"] = "creativeCommon"
    data = _get("search", params)
    ids = [it["id"]["videoId"] for it in data.get("items", []) if it.get("id", {}).get("videoId")]
    items = video_details(ids) if ids else []
    for it in items:
        it["topic"] = topic
    # trend search: ignore videos without real reach (owned/authorized channels are never filtered)
    return [it for it in items if it["views"] >= min_views or it["rights_status"] in {"owned", "authorized"}]


def channel_uploads(channel_id: str, max_results: int = 15) -> list[dict]:
    """Cheap (2–3 units) listing of a channel's latest uploads – used for owned/authorized channels."""
    ch = _get("channels", {"part": "contentDetails", "id": channel_id})
    if not ch.get("items"):
        return []
    playlist = ch["items"][0]["contentDetails"]["relatedPlaylists"]["uploads"]
    pl = _get("playlistItems", {"part": "contentDetails", "playlistId": playlist, "maxResults": min(50, max_results)})
    ids = [x["contentDetails"]["videoId"] for x in pl.get("items", [])]
    items = video_details(ids) if ids else []
    for it in items:
        it["topic"] = "channel:" + channel_id
    return items


def discover(settings: dict) -> list[dict]:
    e = env()
    if not e.youtube_api_key:
        log.info("YOUTUBE_API_KEY not set – skipping YouTube discovery")
        return []
    results: list[dict] = []
    # 1) owned + authorized channels first (cheap, and always processable)
    from ..creators import approved_ids
    for ch in dict.fromkeys(e.owned_youtube_channels + e.authorized_youtube_channels + approved_ids("youtube")):
        try:
            ups = channel_uploads(ch)
            if settings.get("skip_creator_shorts", True) and ch not in e.owned_youtube_channels:
                # their own Shorts are already short-form – re-posting them adds nothing and duplicates them
                ups = [u for u in ups if (u.get("duration_s") or 0) > 180]
            results += ups
        except QuotaExceeded as ex:
            log.warning("quota: %s", ex)
            return results
        except requests.RequestException as ex:
            log.warning("channel %s failed: %s", ch, ex)
    # 2) topic search (100 units each) – learning engine orders topics by past performance
    from ..analysis.learning import feature_adjustment
    topics = sorted(settings.get("topics", []), key=lambda t: -feature_adjustment("topic", t))
    for topic in topics:
        try:
            results += search_topic(topic, settings["discovery_lookback_days"],
                                    settings["discovery_max_results_per_topic"],
                                    settings.get("youtube_creative_commons_only", True),
                                    int(settings.get("discovery_min_views", 1000)))
        except QuotaExceeded as ex:
            log.warning("quota: %s", ex)
            break
        except requests.RequestException as ex:
            log.warning("search '%s' failed: %s", topic, ex)
    return results
