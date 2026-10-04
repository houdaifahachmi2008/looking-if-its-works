import sqlite3
from contextlib import contextmanager

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id            INTEGER PRIMARY KEY,
    osm_id        TEXT UNIQUE,
    name          TEXT NOT NULL,
    category      TEXT,
    street        TEXT,
    postcode      TEXT,
    city          TEXT,
    phone         TEXT,
    email         TEXT,
    email_kind    TEXT,          -- generic | uncertain | personal
    website       TEXT,
    facebook      TEXT,
    lang          TEXT,          -- nl | fr
    situation     TEXT,          -- no_website | facebook_only | outdated | ok | unreachable
    issues        TEXT,          -- JSON-lijst met gevonden problemen
    score         INTEGER,       -- 0-100: hoe sterk heeft dit bedrijf een nieuwe site nodig
    status        TEXT NOT NULL DEFAULT 'new',  -- new | audited | drafted | approved | sent | skipped
    subject       TEXT,
    body          TEXT,
    sent_at       TEXT,
    created_at    TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS suppression (
    value      TEXT PRIMARY KEY,  -- e-mailadres of @domein
    reason     TEXT,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
"""


@contextmanager
def connect():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def is_suppressed(conn, email: str) -> bool:
    email = email.lower().strip()
    domain = "@" + email.split("@")[-1]
    row = conn.execute(
        "SELECT 1 FROM suppression WHERE value IN (?, ?)", (email, domain)
    ).fetchone()
    return row is not None


def suppress(conn, value: str, reason: str = "afgemeld") -> None:
    conn.execute(
        "INSERT OR REPLACE INTO suppression (value, reason) VALUES (?, ?)",
        (value.lower().strip(), reason),
    )
    conn.execute(
        "UPDATE leads SET status='skipped' WHERE lower(email)=? AND status!='sent'",
        (value.lower().strip(),),
    )
