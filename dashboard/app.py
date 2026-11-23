"""ViralBot dashboard — run with:  streamlit run dashboard/app.py"""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from viralbot import db, rights  # noqa: E402
from viralbot.config import YOUTUBE_CATEGORIES, env  # noqa: E402
from viralbot.pipeline import counts, set_clip_decision  # noqa: E402
from viralbot.scheduler import worker_alive  # noqa: E402

st.set_page_config(page_title="ViralBot", page_icon="🎬", layout="wide")
db.init_db()

ACCENT = "#7C5CFF"
st.markdown(f"""
<style>
  .block-container {{padding-top: 1.6rem; max-width: 1400px;}}
  [data-testid="stSidebar"] {{background: #0B0E14; border-right: 1px solid #1F2633;}}
  .kpi {{background: linear-gradient(180deg,#161B26 0%,#121620 100%); border:1px solid #232B3A; border-radius:14px;
         padding:16px 18px;}}
  .kpi .l {{color:#8B93A7; font-size:.78rem; text-transform:uppercase; letter-spacing:.06em;}}
  .kpi .v {{color:#F2F4F8; font-size:1.7rem; font-weight:700; margin-top:4px;}}
  .kpi .s {{color:#6E778C; font-size:.78rem;}}
  .pill {{display:inline-block; padding:2px 10px; border-radius:999px; font-size:.75rem; font-weight:600;}}
  .ok {{background:#12361F; color:#5BE08A;}} .warn {{background:#3A2E10; color:#F5C451;}}
  .bad {{background:#3B1418; color:#FF6B7A;}} .info {{background:#1E1A3A; color:#A996FF;}}
  h1, h2, h3 {{letter-spacing:-.01em;}}
</style>""", unsafe_allow_html=True)

PLOT = dict(template="plotly_dark", paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            margin=dict(l=10, r=10, t=40, b=10), colorway=[ACCENT, "#2EC4B6", "#FF9F1C", "#E71D36", "#5BE08A"])


def df(sql: str, params=()) -> pd.DataFrame:
    return pd.DataFrame([dict(r) for r in db.q(sql, params)])


def kpi(col, label, value, sub=""):
    col.markdown(f'<div class="kpi"><div class="l">{label}</div><div class="v">{value}</div>'
                 f'<div class="s">{sub}</div></div>', unsafe_allow_html=True)


def pill(text, kind):
    return f'<span class="pill {kind}">{text}</span>'


RISK_PILL = {"low": "ok", "medium": "warn", "high": "bad"}


def worker_controls(key: str):
    alive = worker_alive()
    enabled = bool(db.get_setting("bot_enabled"))
    hb = db.get_setting("worker_heartbeat") or {}
    state = hb.get("state", "not running") if alive else "not running"
    st.markdown(f"**Bot:** {pill('ENABLED', 'ok') if enabled else pill('STOPPED', 'bad')} &nbsp; "
                f"**Worker:** {pill(state, 'info' if alive else 'warn')}", unsafe_allow_html=True)
    c1, c2, c3 = st.columns(3)
    if c1.button("▶ Start bot", key=f"start{key}", disabled=enabled and alive, width="stretch"):
        db.set_setting("bot_enabled", True)
        if not worker_alive():
            log = open(env().dir("logs") / "worker.out", "a")
            subprocess.Popen([sys.executable, "-m", "viralbot", "run"], cwd=str(ROOT), stdout=log, stderr=log,
                             start_new_session=True)
        st.rerun()
    if c2.button("⏸ Stop bot", key=f"stop{key}", disabled=not enabled, width="stretch"):
        db.set_setting("bot_enabled", False)
        st.rerun()
    if c3.button("⟳ Run one cycle", key=f"cycle{key}", width="stretch",
                 help="Discovery + processing once, in the background"):
        log = open(env().dir("logs") / "worker.out", "a")
        subprocess.Popen(f"{sys.executable} -m viralbot discover && {sys.executable} -m viralbot process --loops 2",
                         shell=True, cwd=str(ROOT), stdout=log, stderr=log, start_new_session=True)
        st.toast("Cycle started — refresh in a minute")


# ------------------------------------------------------------------ pages

def page_overview():
    st.title("Overview")
    c = counts()
    views = int(db.scalar("""SELECT SUM(a.views) FROM analytics a WHERE a.id IN
                             (SELECT MAX(id) FROM analytics GROUP BY upload_id)""", (), 0))
    cost = float(db.scalar("SELECT SUM(cost_usd) FROM api_costs", (), 0.0))
    cost30 = float(db.scalar("SELECT SUM(cost_usd) FROM api_costs WHERE ts >= date('now','-30 day')", (), 0.0))
    analyzed = int(db.scalar("SELECT COUNT(*) FROM sources WHERE status IN ('analyzed')", (), 0))
    cols = st.columns(6)
    kpi(cols[0], "Discovered", f"{c['discovered']:,}", f"{c['processable']} rights-cleared")
    kpi(cols[1], "Analyzed", f"{analyzed:,}", "sources scored")
    kpi(cols[2], "Clips generated", f"{c['clips']:,}", f"{c['pending_approval']} awaiting review")
    kpi(cols[3], "Published", f"{c['uploaded']:,}", f"{c['approved']} queued")
    kpi(cols[4], "Total views", f"{views:,}", "latest snapshot")
    kpi(cols[5], "AI/API cost", f"${cost:,.2f}", f"${cost30:,.2f} last 30d")
    st.write("")
    worker_controls("ov")
    st.divider()

    left, right = st.columns(2)
    clips = df("SELECT id, status, viral_score, adjusted_score, duration_s, topic, created_at FROM clips")
    if not clips.empty:
        fig = px.histogram(clips, x="adjusted_score", nbins=20, title="Viral score distribution", color="status")
        fig.add_vline(x=float(db.get_setting("min_viral_score")), line_dash="dash", line_color="#FF9F1C")
        fig.update_layout(**PLOT)
        left.plotly_chart(fig, width="stretch")
        st_counts = clips.groupby("status").size().reset_index(name="n")
        fig2 = px.bar(st_counts, x="status", y="n", title="Clips by status")
        fig2.update_layout(**PLOT)
        right.plotly_chart(fig2, width="stretch")
    else:
        left.info("No clips yet. Drop a video you own into data/inbox or run `python -m viralbot demo`.")

    st.subheader("Best-performing clips")
    best = df("""SELECT c.id, c.title, u.youtube_video_id, a.views, a.likes, a.comments, a.avg_view_pct, c.adjusted_score
                 FROM uploads u JOIN clips c ON c.id=u.clip_id
                 JOIN analytics a ON a.id=(SELECT MAX(id) FROM analytics WHERE upload_id=u.id)
                 ORDER BY a.views DESC LIMIT 10""")
    if best.empty:
        best = df("SELECT id, title, status, adjusted_score AS viral_score, duration_s FROM clips "
                  "WHERE file_path IS NOT NULL ORDER BY adjusted_score DESC LIMIT 10")
        st.caption("No analytics yet — showing top clips by viral score.")
    st.dataframe(best, width="stretch", hide_index=True)

    st.subheader("Recent errors")
    ev = df("SELECT ts, level, stage, message FROM events WHERE level IN ('WARNING','ERROR','CRITICAL') "
            "ORDER BY id DESC LIMIT 15")
    if ev.empty:
        st.success("No errors logged.")
    else:
        st.dataframe(ev, width="stretch", hide_index=True)


def _clip_card(c: dict, editable: bool):
    src = dict(db.q1("SELECT * FROM sources WHERE id=?", (c["source_id"],)) or {})
    scores = json.loads(c.get("scores_json") or "{}")
    warnings = json.loads(c.get("warnings_json") or "[]")
    qc = json.loads(c.get("qc_json") or "{}")
    vcol, icol = st.columns([1, 2])
    with vcol:
        if c.get("file_path") and Path(c["file_path"]).exists():
            st.video(c["file_path"])
        else:
            st.info("not rendered")
    with icol:
        risk = c.get("risk_level") or "?"
        st.markdown(f"### #{c['id']} · {c.get('title') or '(untitled)'}")
        score = c.get("adjusted_score") or 0
        st.markdown(" ".join([pill(c["status"], "info"), pill(f"risk: {risk}", RISK_PILL.get(risk, "warn")),
                              pill(f"rights: {src.get('rights_status') or '?'}", "info"),
                              pill(f"score {score:.1f}", "ok" if score >= 75 else "warn")]),
                    unsafe_allow_html=True)
        st.caption(f"Source: {src.get('title', '')} — {src.get('channel_title', '')} · "
                   f"{c['start_s']:.1f}s → {c['end_s']:.1f}s · {c.get('duration_s') or 0:.1f}s · "
                   f"{c.get('layout') or ''}/{c.get('zoom_style') or ''} · {scores.get('scored_by', '')}")
        sc = st.columns(5)
        for col, (k, hi) in zip(sc, [("hook", 25), ("entertainment", 25), ("emotional", 20), ("retention", 20), ("quality", 10)]):
            col.metric(k.capitalize(), f"{scores.get(k, 0):.0f}/{hi}")
        st.caption(c.get("score_reason") or "")
        for w in warnings:
            st.warning(w, icon="⚠️")
        failed = [f"{k}: {v['detail']}" for k, v in qc.items() if not v["ok"]]
        if failed:
            st.error("QC failed — " + "; ".join(failed))
        if editable:
            with st.form(f"f{c['id']}"):
                title = st.text_input("Title", c.get("title") or "", max_chars=95)
                desc = st.text_area("Description", c.get("description") or "", height=130)
                cat = st.selectbox("Category", list(YOUTUBE_CATEGORIES), format_func=lambda k: YOUTUBE_CATEGORIES[k],
                                   index=list(YOUTUBE_CATEGORIES).index(c.get("category_id") or "24"))
                commentary = st.text_area("Your original commentary (shown in description; strengthens monetization "
                                          "eligibility for third-party content)", c.get("commentary") or "", height=70)
                b1, b2, b3 = st.columns(3)
                save = b1.form_submit_button("💾 Save")
                appr = b2.form_submit_button("✅ Approve", type="primary", disabled=risk == "high")
                rej = b3.form_submit_button("❌ Reject")
            if save or appr:
                db.update("clips", c["id"], title=title, description=desc, category_id=cat, commentary=commentary)
            if appr:
                try:
                    set_clip_decision(c["id"], True)
                    st.success("Approved — it will upload on the next cycle (respecting daily limit).")
                except ValueError as ex:
                    st.error(str(ex))
                st.rerun()
            if rej:
                set_clip_decision(c["id"], False, "rejected in dashboard")
                st.rerun()
        with st.expander("Transcript"):
            st.write(c.get("transcript_text") or "")


def page_review():
    st.title("Review queue")
    rows = [dict(r) for r in db.q("SELECT * FROM clips WHERE status='pending_approval' ORDER BY adjusted_score DESC")]
    st.caption(f"{len(rows)} clips waiting. Approved clips upload as "
               f"**{db.get_setting('default_privacy')}** (public requires the extra approval in Settings).")
    if not rows:
        st.success("Nothing to review 🎉")
    for c in rows:
        with st.container(border=True):
            _clip_card(c, editable=True)


def page_clips():
    st.title("Clips")
    statuses = [r[0] for r in db.q("SELECT DISTINCT status FROM clips")]
    sel = st.multiselect("Status", statuses, default=[s for s in statuses if s != "below_threshold"])
    if not sel:
        return
    data = df(f"SELECT id, source_id, status, adjusted_score, viral_score, duration_s, layout, topic, title, risk_level, "
              f"error, updated_at FROM clips WHERE status IN ({','.join('?' * len(sel))}) ORDER BY id DESC", sel)
    st.dataframe(data, width="stretch", hide_index=True)
    if not data.empty:
        cid = st.selectbox("Open clip", data["id"].tolist())
        c = dict(db.q1("SELECT * FROM clips WHERE id=?", (int(cid),)))
        with st.container(border=True):
            _clip_card(c, editable=c["status"] in {"pending_approval", "rejected", "qc_failed"})


def page_sources():
    st.title("Sources & rights")
    st.info("Only **owned / licensed / creative_commons / authorized** sources are ever downloaded or processed. "
            "Everything else is stored as a *lead* (trend signal, or a creator to ask for permission).", icon="⚖️")
    f = st.radio("Show", ["All", "Rights-cleared", "Leads"], horizontal=True)
    where = {"All": "", "Rights-cleared": "WHERE rights_status IN ('owned','licensed','creative_commons','authorized')",
             "Leads": "WHERE status='lead'"}[f]
    data = df(f"SELECT id, platform, title, channel_title, views, views_per_hour, engagement_rate, growth_per_hour, "
              f"discovery_score, topic, rights_status, status, error, url FROM sources {where} "
              f"ORDER BY discovery_score DESC LIMIT 500")
    st.dataframe(data, width="stretch", hide_index=True,
                 column_config={"url": st.column_config.LinkColumn("url"),
                                "discovery_score": st.column_config.ProgressColumn("score", min_value=0, max_value=100, format="%.0f")})
    st.subheader("Update rights status")
    with st.form("rights"):
        c1, c2 = st.columns([1, 2])
        sid = c1.number_input("Source ID", min_value=1, step=1)
        status = c1.selectbox("New status", rights.ALL_STATUSES)
        evidence = c2.text_area("Evidence (license URL, permission e-mail reference, contract ID…)", height=100)
        if st.form_submit_button("Save rights decision"):
            if status in {"authorized", "licensed"} and not evidence.strip():
                st.error("Evidence is required for authorized/licensed content.")
            else:
                try:
                    rights.set_rights(int(sid), status, evidence, actor="dashboard")
                    st.success("Saved and logged.")
                except KeyError:
                    st.error("Unknown source id")
    log = df("SELECT ts, source_id, old_status, new_status, evidence, actor FROM rights_log ORDER BY id DESC LIMIT 50")
    if not log.empty:
        st.caption("Rights audit log")
        st.dataframe(log, width="stretch", hide_index=True)


def page_analytics():
    st.title("Analytics & learning")
    perf = df("""SELECT c.id, c.title, c.topic, c.duration_s, c.layout, c.zoom_style, c.hook_type, c.adjusted_score,
                        a.views, a.likes, a.comments, a.subscribers_gained, a.avg_view_duration_s, a.avg_view_pct
                 FROM uploads u JOIN clips c ON c.id=u.clip_id
                 JOIN analytics a ON a.id=(SELECT MAX(id) FROM analytics WHERE upload_id=u.id)""")
    if perf.empty:
        st.info("No analytics yet. Once clips are uploaded, stats are collected every "
                f"{db.get_setting('analytics_interval_hours')}h (or run `python -m viralbot analytics`).")
    else:
        cols = st.columns(4)
        kpi(cols[0], "Views", f"{int(perf['views'].sum()):,}")
        kpi(cols[1], "Likes", f"{int(perf['likes'].sum()):,}")
        kpi(cols[2], "Subscribers gained", f"{int(perf['subscribers_gained'].fillna(0).sum()):,}")
        kpi(cols[3], "Avg % viewed", f"{perf['avg_view_pct'].mean():.0f}%" if perf['avg_view_pct'].notna().any() else "n/a")
        perf["length"] = (perf["duration_s"] // 5 * 5).astype(int).astype(str) + "s+"
        c1, c2 = st.columns(2)
        for col, dim, title in [(c1, "topic", "Views by topic"), (c2, "length", "Views by clip length"),
                                (c1, "layout", "Views by editing layout"), (c2, "hook_type", "Views by hook type")]:
            g = perf.groupby(dim, dropna=False)["views"].mean().reset_index()
            fig = px.bar(g, x=dim, y="views", title=title)
            fig.update_layout(**PLOT)
            col.plotly_chart(fig, width="stretch")
        fig = px.scatter(perf, x="adjusted_score", y="views", hover_data=["title"], title="Predicted score vs actual views")
        fig.update_layout(**PLOT)
        st.plotly_chart(fig, width="stretch")
        st.dataframe(perf.sort_values("views", ascending=False), width="stretch", hide_index=True)
    st.subheader("Learned adjustments")
    lw = df("SELECT feature, value, n, ROUND(mean_perf,2) AS mean_z, ROUND(adjustment,2) AS score_adjustment "
            "FROM learning_weights ORDER BY feature, adjustment DESC")
    if lw.empty:
        st.caption(f"Learning activates after {db.get_setting('learning_min_samples')} clips have analytics.")
    else:
        st.dataframe(lw, width="stretch", hide_index=True)


def page_costs_logs():
    st.title("Costs, quota & logs")
    e = env()
    quota = db.youtube_quota_used_today()
    cols = st.columns(3)
    kpi(cols[0], "YouTube quota today", f"{quota:,.0f} / {e.youtube_daily_quota:,}", "units (upload ≈ 1,600)")
    kpi(cols[1], "AI spend (all time)", f"${float(db.scalar('SELECT SUM(cost_usd) FROM api_costs', (), 0.0)):.4f}")
    llm_calls = int(db.scalar("SELECT COUNT(*) FROM api_costs WHERE unit_type='tokens'", (), 0))
    kpi(cols[2], "LLM calls", f"{llm_calls:,}")
    costs = df("SELECT substr(ts,1,10) AS day, provider, purpose, SUM(cost_usd) AS usd, SUM(units) AS units, unit_type "
               "FROM api_costs GROUP BY day, provider, purpose, unit_type ORDER BY day DESC")
    if not costs.empty:
        fig = px.bar(costs[costs.unit_type != "quota"], x="day", y="usd", color="purpose", title="Estimated AI cost per day")
        fig.update_layout(**PLOT)
        st.plotly_chart(fig, width="stretch")
        st.dataframe(costs, width="stretch", hide_index=True)
    st.subheader("Event log")
    lvl = st.multiselect("Level", ["INFO", "WARNING", "ERROR", "CRITICAL"], default=["WARNING", "ERROR", "CRITICAL"])
    if lvl:
        st.dataframe(df(f"SELECT ts, level, stage, message FROM events WHERE level IN ({','.join('?' * len(lvl))}) "
                        f"ORDER BY id DESC LIMIT 300", lvl), width="stretch", hide_index=True)
    errs = df("SELECT 'source' AS kind, id, status, error, updated_at FROM sources WHERE error IS NOT NULL "
              "UNION ALL SELECT 'clip', id, status, error, updated_at FROM clips WHERE error IS NOT NULL "
              "ORDER BY updated_at DESC LIMIT 100")
    if not errs.empty:
        st.subheader("Items with processing errors")
        st.dataframe(errs, width="stretch", hide_index=True)


def page_settings():
    st.title("Settings")
    s = db.all_settings()
    worker_controls("set")
    st.divider()
    with st.form("settings"):
        st.subheader("Discovery")
        c1, c2 = st.columns(2)
        src = s["sources_enabled"]
        src_new = {
            "local_inbox": c1.toggle("Local inbox (your own videos)", src.get("local_inbox", True)),
            "youtube": c1.toggle("YouTube Data API", src.get("youtube", True)),
            "twitch": c1.toggle("Twitch API", src.get("twitch", True)),
        }
        cc_only = c1.toggle("YouTube search: Creative Commons only", s["youtube_creative_commons_only"],
                            help="Non-CC results are never processed anyway; turning this off just collects more leads.")
        topics = c2.text_area("Search topics (one per line)", "\n".join(s["topics"]), height=170)
        games = c2.text_input("Twitch categories (comma separated)", ", ".join(s["twitch_game_names"]))
        c3, c4, c5 = st.columns(3)
        interval = c3.number_input("Discovery every (hours)", 1, 48, int(s["discovery_interval_hours"]))
        lookback = c4.number_input("Look back (days)", 1, 30, int(s["discovery_lookback_days"]))
        per_topic = c5.number_input("Results per topic", 1, 50, int(s["discovery_max_results_per_topic"]))
        min_views = c3.number_input("Min. views (search results)", 0, 10_000_000, int(s["discovery_min_views"]), step=500)

        st.subheader("Clip selection & editing")
        c1, c2, c3 = st.columns(3)
        min_score = c1.slider("Minimum viral score", 0, 100, int(s["min_viral_score"]))
        per_source = c2.number_input("Max clips per source", 1, 10, int(s["max_clips_per_source"]))
        target = c3.slider("Target length (s)", 20, 45, int(s["target_clip_seconds"]))
        zoom = c1.selectbox("Zoom style", ["punch", "subtle", "none"], ["punch", "subtle", "none"].index(s["zoom_style"]))
        layout = c2.selectbox("Layout", ["auto", "face_crop", "blur_fill"], ["auto", "face_crop", "blur_fill"].index(s["layout"]))
        banner = c3.toggle("Headline banner", s["add_hook_banner"])
        rm_sil = c1.toggle("Remove silences", s["remove_silence"])
        wpl = c2.number_input("Subtitle words per line", 1, 6, int(s["subtitle_words_per_line"]))

        st.subheader("Publishing")
        c1, c2, c3 = st.columns(3)
        manual = c1.toggle("Require manual approval", s["require_manual_approval"])
        auto_med = c1.toggle("Auto-approve medium-risk (CC/authorized)", s["auto_approve_medium_risk"])
        daily = c2.number_input("Max daily uploads", 0, 50, int(s["max_daily_uploads"]),
                                help="Default API quota (10k units) allows ~5 uploads/day.")
        privacy = c2.selectbox("Upload privacy", ["private", "unlisted", "public"],
                               ["private", "unlisted", "public"].index(s["default_privacy"]))
        public_ok = c3.checkbox("I approve automatic PUBLIC publishing", s["public_publishing_approved"],
                                help="Second safety gate. Without it, 'public' uploads are forced to private.")
        sched = c3.toggle("Schedule public uploads into time slots", s["schedule_uploads"])
        slots = c3.text_input("Slots (UTC, comma separated)", ", ".join(s["upload_slots_utc"]))
        thumb = c2.toggle("Set custom thumbnail (verified channels)", s["set_custom_thumbnail"])

        if st.form_submit_button("Save settings", type="primary"):
            new = {
                "sources_enabled": src_new, "youtube_creative_commons_only": cc_only,
                "topics": [t.strip() for t in topics.splitlines() if t.strip()],
                "twitch_game_names": [g.strip() for g in games.split(",") if g.strip()],
                "discovery_interval_hours": int(interval), "discovery_lookback_days": int(lookback),
                "discovery_max_results_per_topic": int(per_topic), "discovery_min_views": int(min_views), "min_viral_score": int(min_score),
                "max_clips_per_source": int(per_source), "target_clip_seconds": int(target), "zoom_style": zoom,
                "layout": layout, "add_hook_banner": banner, "remove_silence": rm_sil, "subtitle_words_per_line": int(wpl),
                "require_manual_approval": manual, "auto_approve_medium_risk": auto_med, "max_daily_uploads": int(daily),
                "default_privacy": privacy, "public_publishing_approved": public_ok, "schedule_uploads": sched,
                "upload_slots_utc": [x.strip() for x in slots.split(",") if x.strip()], "set_custom_thumbnail": thumb,
            }
            for k, v in new.items():
                db.set_setting(k, v)
            st.success("Saved. The worker picks up changes on its next cycle.")

    st.subheader("Integrations")
    from viralbot.publishing.youtube_auth import is_authorized
    e = env()
    status = {
        "LLM provider": e.active_llm, "Transcription": e.transcribe_engine,
        "YouTube API key (discovery)": bool(e.youtube_api_key), "YouTube upload authorized": is_authorized(),
        "Twitch API": bool(e.twitch_client_id and e.twitch_client_secret), "yt-dlp import enabled": e.enable_ytdlp,
    }
    st.dataframe(pd.DataFrame(status.items(), columns=["integration", "status"]).astype(str), hide_index=True,
                 width="stretch")
    st.caption("API keys are read from .env and are never stored in the database or shown here.")


def page_compliance():
    st.title("Copyright & monetization")
    for n in rights.MONETIZATION_NOTES:
        st.markdown(f"- {n}")
    risk = df("SELECT risk_level, COUNT(*) AS clips FROM clips WHERE risk_level IS NOT NULL GROUP BY risk_level")
    if not risk.empty:
        fig = px.pie(risk, names="risk_level", values="clips", title="Clips by copyright risk", hole=.5,
                     color="risk_level", color_discrete_map={"low": "#5BE08A", "medium": "#F5C451", "high": "#FF6B7A"})
        fig.update_layout(**PLOT)
        st.plotly_chart(fig, width="stretch")
    by_rights = df("SELECT rights_status, COUNT(*) AS sources FROM sources GROUP BY rights_status")
    if not by_rights.empty:
        st.dataframe(by_rights, hide_index=True, width="stretch")


PAGES = {"📊 Overview": page_overview, "📝 Review queue": page_review, "🎞 Clips": page_clips,
         "🔎 Sources & rights": page_sources, "📈 Analytics": page_analytics, "💸 Costs & logs": page_costs_logs,
         "⚙️ Settings": page_settings, "⚖️ Compliance": page_compliance}

with st.sidebar:
    st.markdown(f"<h2 style='margin-bottom:0'>🎬 ViralBot</h2><div style='color:#8B93A7'>YouTube Shorts automation</div>",
                unsafe_allow_html=True)
    st.write("")
    pending = counts()["pending_approval"]
    page = st.radio("Navigation", list(PAGES), label_visibility="collapsed",
                    format_func=lambda p: f"{p}  ({pending})" if p.endswith("Review queue") and pending else p)
    st.divider()
    st.caption(f"Data: `{env().data_dir}`")
    st.caption(datetime.now(timezone.utc).strftime("UTC %Y-%m-%d %H:%M"))

PAGES[page]()
