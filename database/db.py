# database/db.py
import sqlite3
import logging
from config import DB_PATH

logger = logging.getLogger(__name__)


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db():
    with get_connection() as conn:
        conn.executescript("""

        CREATE TABLE IF NOT EXISTS users (
            user_id       INTEGER PRIMARY KEY,
            username      TEXT,
            first_name    TEXT,
            tier          TEXT DEFAULT 'free',
            joined_at     TEXT DEFAULT (datetime('now')),
            trial_ends_at TEXT,
            tier_expires_at TEXT        -- NEW: tracks when paid tier expires
        );

        CREATE TABLE IF NOT EXISTS watches (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       INTEGER NOT NULL,
            label         TEXT NOT NULL,
            url           TEXT NOT NULL,
            watch_type    TEXT NOT NULL,
            last_hash     TEXT,
            last_checked  TEXT,
            last_changed  TEXT,
            active        INTEGER DEFAULT 1,
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        );

        CREATE TABLE IF NOT EXISTS change_log (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            watch_id      INTEGER NOT NULL,
            user_id       INTEGER NOT NULL,
            detected_at   TEXT DEFAULT (datetime('now')),
            old_snapshot  TEXT,
            new_snapshot  TEXT,
            ai_summary    TEXT,
            notified      INTEGER DEFAULT 0,
            FOREIGN KEY (watch_id) REFERENCES watches(id)
        );

        CREATE TABLE IF NOT EXISTS payments (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id       INTEGER NOT NULL,
            payment_id    TEXT,
            amount_usd    REAL,
            tier          TEXT,
            status        TEXT DEFAULT 'pending',
            created_at    TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (user_id) REFERENCES users(user_id)
        );

        CREATE INDEX IF NOT EXISTS idx_watches_user_id
            ON watches(user_id);
        CREATE INDEX IF NOT EXISTS idx_watches_active
            ON watches(active);
        CREATE INDEX IF NOT EXISTS idx_change_log_user_id
            ON change_log(user_id);
        CREATE INDEX IF NOT EXISTS idx_change_log_watch_id
            ON change_log(watch_id);
        CREATE INDEX IF NOT EXISTS idx_payments_user_id
            ON payments(user_id);

        """)

    # Safe migration for existing DBs that predate tier_expires_at
    _run_safe_migration("""
        ALTER TABLE users ADD COLUMN tier_expires_at TEXT
        ALTER TABLE watches ADD COLUMN fetch_failures INTEGER DEFAULT 0
    """)
    
    logger.info("Database initialized successfully")


def _run_safe_migration(sql: str):
    """Run a column-add migration safely — ignores error if column exists."""
    try:
        with get_connection() as conn:
            conn.execute(sql)
    except Exception:
        pass  # column already exists