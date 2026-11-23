"""SQLite persistence layer.

SQLite in WAL mode lets the worker and the Streamlit dashboard read/write
concurrently. Every pipeline stage is driven by row ``status`` columns, which
makes the whole system resumable after a crash: a restart simply picks up rows
in the state they were left in (stale ``*_processing`` leases are reset).
"""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterable

from .config import DEFAULT_SETTINGS, env

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT
);

-- Every piece of content the discovery engine finds (or that you import).
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,              -- youtube | twitch | local | url
    external_id TEXT NOT NULL,
    url TEXT,
    title TEXT,
    channel_id TEXT,
    channel_title TEXT,
    published_at TEXT,
    duration_s REAL,
    views INTEGER DEFAULT 0,
    likes INTEGER DEFAULT 0,
    comments INTEGER DEFAULT 0,
    engagement_rate REAL DEFAULT 0,
    views_per_hour REAL DEFAULT 0,
    growth_per_hour REAL,
    discovery_score REAL DEFAULT 0,
    topic TEXT,
    language TEXT DEFAULT 'en',
    -- rights / permission tracking
    rights_status TEXT NOT NULL DEFAULT 'unknown',  -- unknown|owned|licensed|creative_commons|authorized|rejected
    license TEXT,
    attribution TEXT,
    rights_evidence TEXT,
    -- pipeline state
    status TEXT NOT NULL DEFAULT 'discovered',
    -- discovered|lead|queued|importing|imported|transcribing|transcribed|analyzing|analyzed|skipped|error
    local_path TEXT,
    width INTEGER, height INTEGER,
    attempts INTEGER DEFAULT 0,
    error TEXT,
    lease_at TEXT,
    raw_json TEXT,
    created_at TEXT,
    updated_at TEXT,
    UNIQUE(platform, external_id)
);
CREATE INDEX IF NOT EXISTS idx_sources_status ON sources(status);

CREATE TABLE IF NOT EXISTS metric_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    ts TEXT NOT NULL,
    views INTEGER, likes INTEGER, comments INTEGER
);

CREATE TABLE IF NOT EXISTS rights_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    ts TEXT NOT NULL,
    old_status TEXT, new_status TEXT,
    evidence TEXT, actor TEXT
);

CREATE TABLE IF NOT EXISTS transcripts (
    source_id INTEGER PRIMARY KEY REFERENCES sources(id) ON DELETE CASCADE,
    engine TEXT,
    language TEXT,
    text TEXT,
    words_json TEXT,       -- [{"w": str, "s": float, "e": float}]
    segments_json TEXT,    -- [{"text": str, "s": float, "e": float}]
    created_at TEXT
);

CREATE TABLE IF NOT EXISTS clips (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id INTEGER NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    start_s REAL NOT NULL,
    end_s REAL NOT NULL,
    duration_s REAL,
    transcript_text TEXT,
    scores_json TEXT,
    viral_score REAL,
    adjusted_score REAL,
    score_reason TEXT,
    hook_type TEXT,
    topic TEXT,
    layout TEXT,
    zoom_style TEXT,
    emphasis_words TEXT,
    hook_text TEXT,
    commentary TEXT,
    status TEXT NOT NULL DEFAULT 'candidate',
    -- candidate|rendering|rendered|qc_failed|pending_approval|approved|rejected|uploading|uploaded|upload_failed|error
    file_path TEXT,
    thumbnail_path TEXT,
    title TEXT,
    description TEXT,
    hashtags TEXT,
    category_id TEXT,
    qc_json TEXT,
    risk_level TEXT,
    warnings_json TEXT,
    content_hash TEXT,
    phash TEXT,
    attempts INTEGER DEFAULT 0,
    error TEXT,
    lease_at TEXT,
    created_at TEXT,
    updated_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_clips_status ON clips(status);

CREATE TABLE IF NOT EXISTS uploads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    clip_id INTEGER NOT NULL UNIQUE REFERENCES clips(id) ON DELETE CASCADE,
    youtube_video_id TEXT UNIQUE,
    privacy TEXT,
    publish_at TEXT,
    status TEXT,          -- uploaded|failed
    attempts INTEGER DEFAULT 0,
    error TEXT,
    content_hash TEXT,
    uploaded_at TEXT
);

CREATE TABLE IF NOT EXISTS analytics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    upload_id INTEGER NOT NULL REFERENCES uploads(id) ON DELETE CASCADE,
    ts TEXT NOT NULL,
    views INTEGER, likes INTEGER, comments INTEGER,
    subscribers_gained INTEGER,
    avg_view_duration_s REAL,
    avg_view_pct REAL
);

CREATE TABLE IF NOT EXISTS learning_weights (
    feature TEXT NOT NULL,
    value TEXT NOT NULL,
    n INTEGER,
    mean_perf REAL,
    adjustment REAL,
    updated_at TEXT,
    PRIMARY KEY(feature, value)
);

CREATE TABLE IF NOT EXISTS api_costs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    provider TEXT, model TEXT, purpose TEXT,
    units REAL,           -- tokens, minutes or quota units
    unit_type TEXT,
    cost_usd REAL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    level TEXT, stage TEXT, message TEXT
);
"""

_local = threading.local()


def now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def connect() -> sqlite3.Connection:
    path = env().db_path
    conn = getattr(_local, "conn", None)
    if conn is not None and getattr(_local, "path", None) == str(path):
        return conn
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    _local.conn, _local.path = conn, str(path)
    return conn


def close() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None


def init_db() -> None:
    conn = connect()
    conn.executescript(SCHEMA)


@contextmanager
def tx():
    conn = connect()
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise


def q(sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
    return connect().execute(sql, tuple(params)).fetchall()


def q1(sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
    return connect().execute(sql, tuple(params)).fetchone()


def ex(sql: str, params: Iterable[Any] = ()) -> int:
    cur = connect().execute(sql, tuple(params))
    return cur.lastrowid


def scalar(sql: str, params: Iterable[Any] = (), default: Any = 0) -> Any:
    row = q1(sql, params)
    if row is None or row[0] is None:
        return default
    return row[0]


def update(table: str, row_id: int, **fields: Any) -> None:
    if not fields:
        return
    if table in {"sources", "clips"}:
        fields.setdefault("updated_at", now())
    cols = ", ".join(f"{k}=?" for k in fields)
    connect().execute(f"UPDATE {table} SET {cols} WHERE id=?", (*fields.values(), row_id))


# ---------------------------------------------------------------- settings

def get_setting(key: str) -> Any:
    row = q1("SELECT value FROM settings WHERE key=?", (key,))
    if row is None:
        return DEFAULT_SETTINGS.get(key)
    return json.loads(row["value"])


def set_setting(key: str, value: Any) -> None:
    ex(
        "INSERT INTO settings(key,value,updated_at) VALUES(?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
        (key, json.dumps(value), now()),
    )


def all_settings() -> dict:
    out = dict(DEFAULT_SETTINGS)
    for row in q("SELECT key, value FROM settings"):
        out[row["key"]] = json.loads(row["value"])
    return out


# ---------------------------------------------------------------- events & costs

def log_event(level: str, stage: str, message: str) -> None:
    try:
        ex("INSERT INTO events(ts,level,stage,message) VALUES(?,?,?,?)", (now(), level, stage, message[:4000]))
    except sqlite3.Error:
        pass


def record_cost(provider: str, model: str, purpose: str, units: float, unit_type: str, cost_usd: float) -> None:
    ex(
        "INSERT INTO api_costs(ts,provider,model,purpose,units,unit_type,cost_usd) VALUES(?,?,?,?,?,?,?)",
        (now(), provider, model, purpose, units, unit_type, cost_usd),
    )


def youtube_quota_used_today() -> float:
    # YouTube quota resets at midnight Pacific; UTC-day accounting is a safe approximation
    # (we keep a 10% safety margin in QuotaGuard).
    today = datetime.now(timezone.utc).date().isoformat()
    return float(scalar(
        "SELECT SUM(units) FROM api_costs WHERE provider='youtube' AND unit_type='quota' AND substr(ts,1,10)=?",
        (today,), 0,
    ))


def row_to_dict(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None
