"""SQLite persistence layer for the Gemini trend engine.

Tables (created automatically via :func:`init_db`):
    - trend_scan_runs      one row per scan (manual / daily / weekly)
    - events               saved trend/event opportunities + calendar dates
    - design_ideas         design concepts attached to an event
    - seo_keywords         Etsy SEO keywords attached to an event
    - production_tasks     scheduled production milestones for an event
    - legal_risk_checks    IP / legal risk assessment per event

We use SQLite so the tool stays self-contained (no external DB server) and
matches the existing lightweight Python CLI architecture. The schema is
idempotent; calling :func:`init_db` repeatedly is safe and acts as the
migration entrypoint.
"""

import json
import os
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator

from .config import DATABASE_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS trend_scan_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    scan_type    TEXT NOT NULL,            -- manual | daily | weekly
    provider     TEXT NOT NULL DEFAULT 'gemini',
    model        TEXT,
    status       TEXT NOT NULL DEFAULT 'pending', -- pending | success | error
    summary      TEXT,                     -- immediate action plan / notes
    raw_response TEXT,                      -- full JSON returned by Gemini
    error        TEXT,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS events (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id                 INTEGER REFERENCES trend_scan_runs(id) ON DELETE CASCADE,
    name                   TEXT NOT NULL,
    category               TEXT,
    trend_type             TEXT,            -- event | viral | evergreen
    event_date             TEXT,            -- ISO date if known
    target_buyer           TEXT,
    safe_design_angle      TEXT,
    legal_risk             TEXT,            -- Low | Medium | High | Very High
    saturation_risk        TEXT,
    trend_score            INTEGER,
    recommended_action     TEXT,            -- Act Now | Monitor | Skip
    is_major_event         INTEGER DEFAULT 0,
    requires_manual_review INTEGER DEFAULT 0,
    approved               INTEGER DEFAULT 0,
    research_start_date    TEXT,
    design_start_date      TEXT,
    listing_publish_date   TEXT,
    marketing_push_date    TEXT,
    last_useful_upload_date TEXT,
    created_at             TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS design_ideas (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id   INTEGER REFERENCES events(id) ON DELETE CASCADE,
    run_id     INTEGER REFERENCES trend_scan_runs(id) ON DELETE CASCADE,
    concept    TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS seo_keywords (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id   INTEGER REFERENCES events(id) ON DELETE CASCADE,
    run_id     INTEGER REFERENCES trend_scan_runs(id) ON DELETE CASCADE,
    keyword    TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS production_tasks (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id   INTEGER REFERENCES events(id) ON DELETE CASCADE,
    task_type  TEXT NOT NULL,              -- research | design | listing | marketing | last_upload
    due_date   TEXT,
    status     TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS legal_risk_checks (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id               INTEGER REFERENCES events(id) ON DELETE CASCADE,
    run_id                 INTEGER REFERENCES trend_scan_runs(id) ON DELETE CASCADE,
    risk_level             TEXT,            -- Low | Medium | High | Very High
    unsafe_terms           TEXT,            -- JSON array
    safe_alternative       TEXT,
    requires_manual_review INTEGER DEFAULT 0,
    notes                  TEXT,
    created_at             TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS listings (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id           INTEGER REFERENCES events(id) ON DELETE CASCADE,
    title              TEXT,
    description        TEXT,
    tags               TEXT,            -- JSON array
    image_path         TEXT,
    image_prompt       TEXT,
    status             TEXT NOT NULL DEFAULT 'generated', -- generated | blocked | published | error
    blocked_reason     TEXT,
    publish_target     TEXT,            -- printify | etsy
    external_id        TEXT,            -- printify product id
    external_url       TEXT,
    error              TEXT,
    created_at         TEXT NOT NULL DEFAULT (datetime('now')),
    published_at       TEXT
);

CREATE INDEX IF NOT EXISTS idx_listings_event ON listings(event_id);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id);
CREATE INDEX IF NOT EXISTS idx_events_event_date ON events(event_date);
CREATE INDEX IF NOT EXISTS idx_design_ideas_event ON design_ideas(event_id);
CREATE INDEX IF NOT EXISTS idx_seo_keywords_event ON seo_keywords(event_id);
CREATE INDEX IF NOT EXISTS idx_production_tasks_event ON production_tasks(event_id);
CREATE INDEX IF NOT EXISTS idx_legal_checks_event ON legal_risk_checks(event_id);
"""


def _ensure_parent_dir(path: str) -> None:
    parent = os.path.dirname(os.path.abspath(path))
    if parent:
        os.makedirs(parent, exist_ok=True)


@contextmanager
def get_connection(db_path: str | None = None) -> Iterator[sqlite3.Connection]:
    """Yields a SQLite connection with foreign keys + row dict access."""
    path = db_path or DATABASE_PATH
    _ensure_parent_dir(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(db_path: str | None = None) -> None:
    """Create all tables/indexes if they do not exist (idempotent migration)."""
    with get_connection(db_path) as conn:
        conn.executescript(SCHEMA)


# --- Write helpers ----------------------------------------------------------

def create_run(scan_type: str, provider: str, model: str, db_path: str | None = None) -> int:
    with get_connection(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO trend_scan_runs (scan_type, provider, model, status) "
            "VALUES (?, ?, ?, 'pending')",
            (scan_type, provider, model),
        )
        return int(cur.lastrowid)


def finish_run(
    run_id: int,
    status: str,
    summary: str | None = None,
    raw_response: str | None = None,
    error: str | None = None,
    db_path: str | None = None,
) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE trend_scan_runs SET status = ?, summary = ?, raw_response = ?, "
            "error = ?, completed_at = datetime('now') WHERE id = ?",
            (status, summary, raw_response, error, run_id),
        )


def insert_event(run_id: int, event: dict[str, Any], db_path: str | None = None) -> int:
    """Insert an event row. ``event`` keys map directly to columns."""
    columns = [
        "run_id", "name", "category", "trend_type", "event_date", "target_buyer",
        "safe_design_angle", "legal_risk", "saturation_risk", "trend_score",
        "recommended_action", "is_major_event", "requires_manual_review", "approved",
        "research_start_date", "design_start_date", "listing_publish_date",
        "marketing_push_date", "last_useful_upload_date",
    ]
    values = [run_id] + [event.get(c) for c in columns[1:]]
    placeholders = ", ".join(["?"] * len(columns))
    with get_connection(db_path) as conn:
        cur = conn.execute(
            f"INSERT INTO events ({', '.join(columns)}) VALUES ({placeholders})",
            values,
        )
        return int(cur.lastrowid)


def insert_design_ideas(run_id: int, event_id: int, concepts: list[str], db_path: str | None = None) -> None:
    if not concepts:
        return
    with get_connection(db_path) as conn:
        conn.executemany(
            "INSERT INTO design_ideas (run_id, event_id, concept) VALUES (?, ?, ?)",
            [(run_id, event_id, c) for c in concepts if c],
        )


def insert_seo_keywords(run_id: int, event_id: int, keywords: list[str], db_path: str | None = None) -> None:
    if not keywords:
        return
    with get_connection(db_path) as conn:
        conn.executemany(
            "INSERT INTO seo_keywords (run_id, event_id, keyword) VALUES (?, ?, ?)",
            [(run_id, event_id, k) for k in keywords if k],
        )


def insert_production_tasks(event_id: int, tasks: list[dict[str, Any]], db_path: str | None = None) -> None:
    if not tasks:
        return
    with get_connection(db_path) as conn:
        conn.executemany(
            "INSERT INTO production_tasks (event_id, task_type, due_date, status) "
            "VALUES (?, ?, ?, ?)",
            [(event_id, t["task_type"], t.get("due_date"), t.get("status", "pending")) for t in tasks],
        )


def insert_legal_risk_check(run_id: int, event_id: int, check: dict[str, Any], db_path: str | None = None) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            "INSERT INTO legal_risk_checks (run_id, event_id, risk_level, unsafe_terms, "
            "safe_alternative, requires_manual_review, notes) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                run_id,
                event_id,
                check.get("risk_level"),
                json.dumps(check.get("unsafe_terms", [])),
                check.get("safe_alternative"),
                1 if check.get("requires_manual_review") else 0,
                check.get("notes"),
            ),
        )


# --- Read helpers -----------------------------------------------------------

def _row_to_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {k: row[k] for k in row.keys()}


def get_event(event_id: int, db_path: str | None = None) -> dict[str, Any] | None:
    """Return a single event with its design ideas, keywords and legal check."""
    events = list_events(limit=1000, db_path=db_path)
    for ev in events:
        if ev["id"] == event_id:
            return ev
    return None


# --- Listings ---------------------------------------------------------------

def create_listing(listing: dict[str, Any], db_path: str | None = None) -> int:
    with get_connection(db_path) as conn:
        cur = conn.execute(
            "INSERT INTO listings (event_id, title, description, tags, image_path, "
            "image_prompt, status, blocked_reason) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                listing.get("event_id"),
                listing.get("title"),
                listing.get("description"),
                json.dumps(listing.get("tags", [])),
                listing.get("image_path"),
                listing.get("image_prompt"),
                listing.get("status", "generated"),
                listing.get("blocked_reason"),
            ),
        )
        return int(cur.lastrowid)


def mark_listing_published(
    listing_id: int,
    target: str,
    external_id: str | None,
    external_url: str | None,
    db_path: str | None = None,
) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE listings SET status = 'published', publish_target = ?, external_id = ?, "
            "external_url = ?, published_at = datetime('now') WHERE id = ?",
            (target, external_id, external_url, listing_id),
        )


def mark_listing_error(listing_id: int, error: str, db_path: str | None = None) -> None:
    with get_connection(db_path) as conn:
        conn.execute(
            "UPDATE listings SET status = 'error', error = ? WHERE id = ?",
            (error, listing_id),
        )


def get_listing(listing_id: int, db_path: str | None = None) -> dict[str, Any] | None:
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM listings WHERE id = ?", (listing_id,)).fetchone()
        if not row:
            return None
        d = _row_to_dict(row)
        try:
            d["tags"] = json.loads(d.get("tags") or "[]")
        except (json.JSONDecodeError, TypeError):
            d["tags"] = []
        return d


def list_listings(limit: int = 100, db_path: str | None = None) -> list[dict[str, Any]]:
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT l.*, e.name AS event_name FROM listings l "
            "LEFT JOIN events e ON e.id = l.event_id "
            "ORDER BY l.id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        out = []
        for r in rows:
            d = _row_to_dict(r)
            try:
                d["tags"] = json.loads(d.get("tags") or "[]")
            except (json.JSONDecodeError, TypeError):
                d["tags"] = []
            out.append(d)
        return out


def get_run(run_id: int, db_path: str | None = None) -> dict[str, Any] | None:
    with get_connection(db_path) as conn:
        row = conn.execute("SELECT * FROM trend_scan_runs WHERE id = ?", (run_id,)).fetchone()
        return _row_to_dict(row) if row else None


def list_events(
    limit: int = 100,
    upcoming_only: bool = False,
    db_path: str | None = None,
) -> list[dict[str, Any]]:
    """Return saved events with their design ideas, keywords and tasks attached."""
    query = "SELECT * FROM events"
    params: list[Any] = []
    if upcoming_only:
        query += " WHERE event_date IS NOT NULL AND event_date >= date('now')"
    query += " ORDER BY (event_date IS NULL), event_date ASC, trend_score DESC LIMIT ?"
    params.append(limit)

    with get_connection(db_path) as conn:
        events = [_row_to_dict(r) for r in conn.execute(query, params).fetchall()]
        for ev in events:
            eid = ev["id"]
            ev["design_ideas"] = [
                r["concept"]
                for r in conn.execute(
                    "SELECT concept FROM design_ideas WHERE event_id = ?", (eid,)
                ).fetchall()
            ]
            ev["seo_keywords"] = [
                r["keyword"]
                for r in conn.execute(
                    "SELECT keyword FROM seo_keywords WHERE event_id = ?", (eid,)
                ).fetchall()
            ]
            ev["production_tasks"] = [
                _row_to_dict(r)
                for r in conn.execute(
                    "SELECT task_type, due_date, status FROM production_tasks "
                    "WHERE event_id = ? ORDER BY due_date", (eid,)
                ).fetchall()
            ]
            legal = conn.execute(
                "SELECT risk_level, unsafe_terms, safe_alternative, requires_manual_review, notes "
                "FROM legal_risk_checks WHERE event_id = ? LIMIT 1", (eid,)
            ).fetchone()
            if legal:
                d = _row_to_dict(legal)
                try:
                    d["unsafe_terms"] = json.loads(d.get("unsafe_terms") or "[]")
                except (json.JSONDecodeError, TypeError):
                    d["unsafe_terms"] = []
                ev["legal_risk_check"] = d
            else:
                ev["legal_risk_check"] = None
        return events


def list_runs(limit: int = 20, db_path: str | None = None) -> list[dict[str, Any]]:
    with get_connection(db_path) as conn:
        rows = conn.execute(
            "SELECT id, scan_type, provider, model, status, summary, created_at, completed_at "
            "FROM trend_scan_runs ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [_row_to_dict(r) for r in rows]


if __name__ == "__main__":
    init_db()
    print(f"Initialized trend database at {DATABASE_PATH}")
