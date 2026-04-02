# database/db.py
import sqlite3
import logging
from config import DB_PATH

logger = logging.getLogger(__name__)


def get_connection() -> sqlite3.Connection:
    """Create and configure a new SQLite connection."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """Initialize the database schema and run any necessary migrations."""
    with get_connection() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                user_id           INTEGER PRIMARY KEY,
                username          TEXT,
                first_name        TEXT,
                tier              TEXT DEFAULT 'free',
                joined_at         TEXT DEFAULT (datetime('now')),
                trial_ends_at     TEXT,
                tier_expires_at   TEXT,              -- tracks when paid tier expires
                quiet_hours_start INTEGER DEFAULT NULL,
                quiet_hours_end   INTEGER DEFAULT NULL,
                quiet_hours_on    INTEGER DEFAULT 0
            );

            CREATE TABLE IF NOT EXISTS watches (
                id             INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id        INTEGER NOT NULL,
                label          TEXT NOT NULL,
                url            TEXT NOT NULL,
                watch_type     TEXT NOT NULL,
                last_hash      TEXT,
                last_checked   TEXT,
                last_changed   TEXT,
                active         INTEGER DEFAULT 1,
                fetch_failures INTEGER DEFAULT 0,
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

            CREATE INDEX IF NOT EXISTS idx_watches_user_id   ON watches(user_id);
            CREATE INDEX IF NOT EXISTS idx_watches_active     ON watches(active);
            CREATE INDEX IF NOT EXISTS idx_change_log_user_id ON change_log(user_id);
            CREATE INDEX IF NOT EXISTS idx_change_log_watch_id ON change_log(watch_id);
            CREATE INDEX IF NOT EXISTS idx_payments_user_id   ON payments(user_id);
        """)

    # Safe backwards-compatible migrations for older database files
    _run_safe_migration()

    logger.info("Database initialized successfully")


def _run_safe_migration() -> None:
    """Add new columns safely (ignores 'duplicate column name' errors)."""
    migration_statements = [
        "ALTER TABLE users ADD COLUMN tier_expires_at TEXT",
        "ALTER TABLE users ADD COLUMN quiet_hours_start INTEGER DEFAULT NULL",
        "ALTER TABLE users ADD COLUMN quiet_hours_end INTEGER DEFAULT NULL",
        "ALTER TABLE users ADD COLUMN quiet_hours_on INTEGER DEFAULT 0",
        "ALTER TABLE watches ADD COLUMN fetch_failures INTEGER DEFAULT 0",
    ]

    with get_connection() as conn:
        for stmt in migration_statements:
            try:
                conn.execute(stmt)
            except sqlite3.OperationalError as e:
                # Expected when column already exists (new DBs)
                if "duplicate column name" not in str(e).lower():
                    logger.warning(f"Migration failed (non-duplicate error): {stmt} - {e}")
            except Exception as e:  # pragma: no cover
                logger.error(f"Unexpected error during migration {stmt}: {e}")