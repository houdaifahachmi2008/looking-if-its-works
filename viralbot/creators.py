"""Creator permission registry.

Workflow (permission-first clipping):
  1. discovery finds popular videos  -> stored as *leads*
  2. ``suggestions()`` groups leads by channel -> creators worth asking
  3. you ask them (``message_template``) and record the answer here
  4. ``approved`` creators: all their existing and future videos/clips become
     rights_status='authorized' (with your evidence) and are processed automatically.
     Declined/revoked creators are parked again.

Nothing here grants permission by itself - it only records the permission
YOU obtained, so every clip can be traced back to it.
"""
from __future__ import annotations

from . import db, rights

STATUSES = ["to_ask", "asked", "approved", "declined", "revoked"]
PLATFORMS = ["youtube", "twitch"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS creators (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,             -- youtube | twitch
    channel_id TEXT NOT NULL,           -- YouTube UC... id, or Twitch login (lowercase) / broadcaster id
    name TEXT,
    contact TEXT,
    status TEXT NOT NULL DEFAULT 'to_ask',
    evidence TEXT,                      -- where/when permission was given
    attribution TEXT,                   -- credit line the creator asked for
    monetization_allowed INTEGER DEFAULT 0,
    notes TEXT,
    asked_at TEXT, decided_at TEXT,
    created_at TEXT, updated_at TEXT,
    UNIQUE(platform, channel_id)
);
"""


def init() -> None:
    db.connect().executescript(SCHEMA)


def _norm(platform: str, channel_id: str) -> str:
    cid = (channel_id or "").strip()
    return cid.lower().lstrip("@") if platform == "twitch" else cid


def get(platform: str, channel_id: str) -> dict | None:
    init()
    row = db.q1("SELECT * FROM creators WHERE platform=? AND channel_id=?", (platform, _norm(platform, channel_id)))
    return dict(row) if row else None


def approved_ids(platform: str) -> list[str]:
    init()
    return [r["channel_id"] for r in db.q("SELECT channel_id FROM creators WHERE platform=? AND status='approved'",
                                          (platform,))]


def approved_match(platform: str, *candidates: str | None) -> dict | None:
    """Return the approved creator matching any of the given ids/logins (case-insensitive for Twitch)."""
    init()
    for c in candidates:
        if not c:
            continue
        row = db.q1("SELECT * FROM creators WHERE platform=? AND status='approved' AND channel_id=?",
                    (platform, _norm(platform, c)))
        if row:
            return dict(row)
    return None


def upsert(platform: str, channel_id: str, name: str = "", contact: str = "", status: str = "to_ask",
           evidence: str = "", attribution: str = "", monetization_allowed: bool = False, notes: str = "") -> int:
    """Create or update a creator and apply the rights consequences to their sources."""
    if platform not in PLATFORMS:
        raise ValueError(f"platform must be one of {PLATFORMS}")
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    cid = _norm(platform, channel_id)
    if not cid:
        raise ValueError("channel id / login is required")
    if status == "approved" and not evidence.strip():
        raise ValueError("record where/when the creator gave permission (evidence) before approving")
    init()
    prev = get(platform, cid)
    now = db.now()
    asked_at = (prev or {}).get("asked_at") or (now if status in {"asked", "approved", "declined"} else None)
    decided_at = now if status in {"approved", "declined", "revoked"} and (prev or {}).get("status") != status \
        else (prev or {}).get("decided_at")
    db.ex("""INSERT INTO creators(platform,channel_id,name,contact,status,evidence,attribution,monetization_allowed,notes,
                 asked_at,decided_at,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
             ON CONFLICT(platform,channel_id) DO UPDATE SET name=excluded.name, contact=excluded.contact,
                 status=excluded.status, evidence=excluded.evidence, attribution=excluded.attribution,
                 monetization_allowed=excluded.monetization_allowed, notes=excluded.notes,
                 asked_at=excluded.asked_at, decided_at=excluded.decided_at, updated_at=excluded.updated_at""",
          (platform, cid, name, contact, status, evidence, attribution, int(monetization_allowed), notes,
           asked_at, decided_at, now, now))
    apply_rights(platform, cid)
    return int(db.scalar("SELECT id FROM creators WHERE platform=? AND channel_id=?", (platform, cid)))


def _source_ids_for(platform: str, cid: str) -> list[int]:
    if platform == "twitch":
        rows = db.q("SELECT id FROM sources WHERE platform='twitch' AND (lower(channel_title)=? OR channel_id=?)",
                    (cid, cid))
    else:
        rows = db.q("SELECT id FROM sources WHERE platform='youtube' AND channel_id=?", (cid,))
    return [r["id"] for r in rows]


def default_attribution(c: dict, source: dict | None = None) -> str:
    if c.get("attribution"):
        return c["attribution"]
    name = c.get("name") or c["channel_id"]
    if c["platform"] == "twitch":
        return f"Clip from {name} – twitch.tv/{c['channel_id']} (used with permission)"
    url = (source or {}).get("url") or f"https://www.youtube.com/channel/{c['channel_id']}"
    return f"Original video: {name} – {url} (used with permission)"


def apply_rights(platform: str, channel_id: str) -> int:
    """Sync the rights status of every known source of this creator. Returns #sources changed."""
    c = get(platform, channel_id)
    if c is None:
        return 0
    changed = 0
    for sid in _source_ids_for(platform, c["channel_id"]):
        src = dict(db.q1("SELECT * FROM sources WHERE id=?", (sid,)))
        if src["rights_status"] in {"owned", "licensed"}:
            continue  # never downgrade stronger rights
        if c["status"] == "approved" and src["rights_status"] != "authorized":
            rights.set_rights(sid, "authorized", f"Creator permission: {c['evidence']}", actor="creators",
                              attribution=default_attribution(c, src))
            changed += 1
        elif c["status"] in {"declined", "revoked"} and src["rights_status"] == "authorized":
            rights.set_rights(sid, "unknown", f"Creator permission {c['status']}", actor="creators")
            changed += 1
    if c["status"] in {"declined", "revoked"}:
        # stop anything from this creator that is not yet published
        for sid in _source_ids_for(platform, c["channel_id"]):
            db.connect().execute("UPDATE clips SET status='rejected', error='creator permission withdrawn' "
                                 "WHERE source_id=? AND status IN ('candidate','rendered','pending_approval','approved')",
                                 (sid,))
    return changed


def rights_for_item(platform: str, channel_id: str | None, channel_login: str | None = None) -> dict | None:
    """Used by discovery: rights fields for an item from an approved creator, else None."""
    c = approved_match(platform, channel_id, channel_login)
    if not c:
        return None
    return {"rights_status": "authorized", "rights_evidence": f"Creator permission: {c['evidence']}",
            "attribution": default_attribution(c)}


def suggestions(limit: int = 50) -> list[dict]:
    """Popular channels among discovered leads that are not in the registry yet."""
    init()
    rows = db.q("""
        SELECT s.platform, CASE WHEN s.platform='twitch' THEN lower(s.channel_title) ELSE s.channel_id END AS cid,
               MAX(s.channel_title) AS name, COUNT(*) AS videos, SUM(s.views) AS total_views,
               ROUND(MAX(s.discovery_score),1) AS best_score, MAX(s.topic) AS topic
        FROM sources s
        WHERE s.status='lead' AND s.platform IN ('youtube','twitch') AND s.channel_id IS NOT NULL
        GROUP BY s.platform, cid
        ORDER BY best_score DESC, total_views DESC LIMIT ?""", (limit * 2,))
    known = {(r["platform"], r["channel_id"]) for r in db.q("SELECT platform, channel_id FROM creators")}
    out = [dict(r) for r in rows if (r["platform"], r["cid"]) not in known]
    return out[:limit]


def message_template(name: str, my_channel: str, lang: str = "nl") -> str:
    name = (name or "").strip()
    if lang == "nl":
        return (f"Hoi{' ' + name if name else ''}!\n\n"
                f"Ik ben de maker van het YouTube Shorts-kanaal {my_channel}. Ik vind je content echt sterk en zou graag "
                f"korte clips (20–45 sec) van je video's/streams maken als YouTube Shorts.\n\n"
                f"Wat je ervan krijgt:\n"
                f"• In elke Short staat jouw naam + een link naar je kanaal in de beschrijving\n"
                f"• Gratis extra bereik bij een nieuw publiek\n"
                f"• Ik knip niets uit context en verander niets aan wat je zegt\n"
                f"• Wil je ooit dat ik stop of een clip verwijder? Eén berichtje is genoeg\n\n"
                f"Mag ik clips van je content maken en op mijn kanaal plaatsen? En is het oké als het kanaal "
                f"daar (later) inkomsten mee verdient?\n\n"
                f"Groetjes!")
    return (f"Hi{' ' + name if name else ''}!\n\n"
            f"I run the YouTube Shorts channel {my_channel}. I really like your content and would love to turn "
            f"short moments (20–45 sec) from your videos/streams into YouTube Shorts.\n\n"
            f"What's in it for you:\n"
            f"• Every Short credits you by name with a link to your channel in the description\n"
            f"• Free extra reach to a new audience\n"
            f"• I never take things out of context or change what you said\n"
            f"• If you ever want me to stop or remove a clip, one message is enough\n\n"
            f"Would you be OK with me clipping your content and posting it on my channel? And is it fine if "
            f"the channel is monetized?\n\n"
            f"Thanks!")


# ------------------------------------------------------------------ Drive folder
def set_drive_folder(platform: str, channel_id: str, link: str) -> None:
    from .discovery.drive import folder_id
    link = (link or "").strip()
    if link and not folder_id(link):
        raise ValueError("That doesn't look like a Google Drive folder link")
    db.ex("UPDATE creators SET drive_folder=?, updated_at=? WHERE platform=? AND channel_id=?",
          (link, db.now(), platform, _norm(platform, channel_id)))


# ------------------------------------------------------------------ follow-ups
def followups_due(days: int = 5, max_followups: int = 2) -> list[dict]:
    """Creators you asked > `days` ago (or last followed up > `days` ago) without an answer."""
    from datetime import datetime, timedelta, timezone
    init()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).replace(microsecond=0).isoformat()
    rows = db.q("""SELECT * FROM creators WHERE status='asked' AND COALESCE(followups,0) < ?
                   AND COALESCE(last_followup_at, asked_at) < ? ORDER BY asked_at""", (max_followups, cutoff))
    return [dict(r) for r in rows]


def mark_followed_up(creator_id: int) -> None:
    db.ex("UPDATE creators SET followups=COALESCE(followups,0)+1, last_followup_at=?, updated_at=? WHERE id=?",
          (db.now(), db.now(), creator_id))


def followup_template(name: str, my_channel: str, lang: str = "nl") -> str:
    name = (name or "").strip()
    if lang == "nl":
        return (f"Hoi{' ' + name if name else ''}! Even een vriendelijke herinnering aan mijn bericht van vorige week 🙂\n\n"
                f"Ik zou graag korte clips van je content maken voor mijn Shorts-kanaal {my_channel}, altijd met jouw "
                f"naam en een link naar je kanaal. Een simpel 'ja' of 'nee' is helemaal oké!\n\nGroetjes!")
    return (f"Hi{' ' + name if name else ''}! Just a friendly follow-up on my message from last week 🙂\n\n"
            f"I'd love to make short clips of your content for my Shorts channel {my_channel}, always crediting you "
            f"with a link to your channel. A simple yes or no is totally fine!\n\nThanks!")


# ------------------------------------------------------------------ scouting
def refresh_channel_stats(channel_ids: list[str]) -> int:
    """Subscriber counts for YouTube channels (1 quota unit per 50 channels)."""
    from .config import env
    from .discovery.youtube import _get
    if not env().youtube_api_key or not channel_ids:
        return 0
    n = 0
    for i in range(0, len(channel_ids), 50):
        data = _get("channels", {"part": "statistics", "id": ",".join(channel_ids[i:i + 50])})
        for it in data.get("items", []):
            st = it.get("statistics", {})
            subs = None if st.get("hiddenSubscriberCount") else int(st.get("subscriberCount") or 0)
            db.ex("INSERT INTO channel_stats(platform,channel_id,subscribers,video_count,updated_at) VALUES('youtube',?,?,?,?) "
                  "ON CONFLICT(platform,channel_id) DO UPDATE SET subscribers=excluded.subscribers, "
                  "video_count=excluded.video_count, updated_at=excluded.updated_at",
                  (it["id"], subs, int(st.get("videoCount") or 0), db.now()))
            n += 1
    return n


def scout(limit: int = 50, min_subs: int | None = None, max_subs: int | None = None, refresh: bool = True) -> list[dict]:
    """Suggestions enriched with subscribers and a 'clipability' estimate, sorted by fit.

    clipability (0-1): long videos/streams = lots of talking material to clip from.
    fit = discovery score (trend) + 40 × clipability, with a bonus for the sweet spot of
    10k–500k subscribers (big enough to be interesting, small enough to say yes).
    """
    sug = suggestions(limit * 2)
    if refresh:
        missing = [s["cid"] for s in sug if s["platform"] == "youtube" and not db.q1(
            "SELECT 1 FROM channel_stats WHERE platform='youtube' AND channel_id=? "
            "AND updated_at > datetime('now','-7 day')", (s["cid"],))]
        try:
            refresh_channel_stats(missing)
        except Exception:
            pass
    out = []
    for s in sug:
        row = db.q1("SELECT subscribers FROM channel_stats WHERE platform=? AND channel_id=?", (s["platform"], s["cid"]))
        subs = row["subscribers"] if row else None
        avg = db.scalar("SELECT AVG(duration_s) FROM sources WHERE platform=? AND "
                        "(channel_id=? OR lower(channel_title)=?)", (s["platform"], s["cid"], s["cid"]), 0) or 0
        clip = round(min(1.0, float(avg) / 1800), 2)
        if min_subs is not None and subs is not None and subs < min_subs:
            continue
        if max_subs is not None and subs is not None and subs > max_subs:
            continue
        sweet = 10 if subs is not None and 10_000 <= subs <= 500_000 else 0
        out.append({**s, "subscribers": subs, "avg_minutes": round(float(avg) / 60, 1), "clipability": clip,
                    "fit": round(float(s["best_score"] or 0) + 40 * clip + sweet, 1)})
    out.sort(key=lambda x: -x["fit"])
    return out[:limit]


# ------------------------------------------------------------------ reports
def report(creator_id: int, days: int = 30) -> dict:
    c = dict(db.q1("SELECT * FROM creators WHERE id=?", (creator_id,)))
    if c["platform"] == "twitch":
        src_filter, params = "s.platform IN ('twitch','drive') AND (lower(s.channel_title)=? OR s.channel_id=?)", \
            (c["channel_id"], c["channel_id"])
    else:
        src_filter, params = "s.platform IN ('youtube','drive') AND s.channel_id=?", (c["channel_id"],)
    rows = [dict(r) for r in db.q(f"""
        SELECT c.title, u.youtube_video_id, u.uploaded_at, a.views, a.likes, a.comments, a.subscribers_gained
        FROM uploads u JOIN clips c ON c.id=u.clip_id JOIN sources s ON s.id=c.source_id
        LEFT JOIN analytics a ON a.id=(SELECT MAX(id) FROM analytics WHERE upload_id=u.id)
        WHERE u.status='uploaded' AND u.uploaded_at >= datetime('now', ?) AND {src_filter}
        ORDER BY a.views DESC""", (f"-{days} day", *params))]
    views = sum(r["views"] or 0 for r in rows)
    return {"creator": c, "days": days, "shorts": len(rows), "views": views,
            "likes": sum(r["likes"] or 0 for r in rows), "comments": sum(r["comments"] or 0 for r in rows),
            "top": rows[0] if rows else None, "rows": rows}


def _n(x: int, lang: str) -> str:
    s = f"{int(x or 0):,}"
    return s.replace(",", ".") if lang == "nl" else s


def report_text(rep: dict, my_channel: str, lang: str = "nl") -> str:
    c, top = rep["creator"], rep["top"]
    name = c.get("name") or c["channel_id"]
    if lang == "nl":
        top_line = f"\n• Best presterende clip: \"{top['title']}\" – {_n(top['views'], lang)} weergaven" if top else ""
        return (f"Hoi {name}! Hier je update van de afgelopen {rep['days']} dagen op {my_channel}:\n\n"
                f"• {rep['shorts']} Shorts met jouw content geplaatst\n"
                f"• {_n(rep['views'], lang)} weergaven in totaal\n"
                f"• {_n(rep['likes'], lang)} likes en {_n(rep['comments'], lang)} reacties{top_line}\n\n"
                f"Elke Short linkt naar jouw kanaal. Bedankt dat ik je content mag clippen! "
                f"Laat het weten als je bepaalde momenten wel of juist niet geclipt wilt hebben.")
    top_line = f"\n• Best performing clip: \"{top['title']}\" – {_n(top['views'], lang)} views" if top else ""
    return (f"Hi {name}! Here's your update for the last {rep['days']} days on {my_channel}:\n\n"
            f"• {rep['shorts']} Shorts posted with your content\n"
            f"• {_n(rep['views'], lang)} views in total\n"
            f"• {_n(rep['likes'], lang)} likes and {_n(rep['comments'], lang)} comments{top_line}\n\n"
            f"Every Short links back to your channel. Thanks for letting me clip your content! "
            f"Let me know if there are moments you'd like (or not like) to see clipped.")
