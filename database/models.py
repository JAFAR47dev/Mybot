# database/models.py
from typing import Optional
from datetime import datetime, timedelta
import sqlite3
from database.db import get_connection


# ─── Users ────────────────────────────────────────────────────────────────────

def upsert_user(user_id: int, username: str, first_name: str):
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO users (user_id, username, first_name)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username   = excluded.username,
                first_name = excluded.first_name
        """, (user_id, username, first_name))


def get_user(user_id: int) -> Optional[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()


def set_user_tier(user_id: int, tier: str, trial_ends_at: str = None):
    expires_at = None if tier == "free" else (
        datetime.utcnow() + timedelta(days=30)
    ).strftime("%Y-%m-%d %H:%M:%S")

    with get_connection() as conn:
        conn.execute("""
            UPDATE users
            SET tier = ?, trial_ends_at = ?, tier_expires_at = ?
            WHERE user_id = ?
        """, (tier, trial_ends_at, expires_at, user_id))


def get_all_users() -> list:
    with get_connection() as conn:
        return conn.execute("SELECT * FROM users").fetchall()


def get_expiring_subscriptions(days_ahead: int) -> list:
    with get_connection() as conn:
        return conn.execute("""
            SELECT * FROM users
            WHERE tier != 'free'
            AND tier_expires_at IS NOT NULL
            AND datetime(tier_expires_at) <= datetime('now', ? || ' days')
            AND datetime(tier_expires_at) > datetime('now')
        """, (str(days_ahead),)).fetchall()


def get_expired_subscriptions() -> list:
    with get_connection() as conn:
        return conn.execute("""
            SELECT * FROM users
            WHERE tier != 'free'
            AND tier_expires_at IS NOT NULL
            AND datetime(tier_expires_at) <= datetime('now')
        """).fetchall()


def downgrade_user(user_id: int):
    with get_connection() as conn:
        conn.execute("""
            UPDATE users
            SET tier = 'free', tier_expires_at = NULL
            WHERE user_id = ?
        """, (user_id,))


# ─── Watches ──────────────────────────────────────────────────────────────────

def add_watch(user_id: int, label: str, url: str, watch_type: str) -> int:
    with get_connection() as conn:
        cursor = conn.execute("""
            INSERT INTO watches (user_id, label, url, watch_type)
            VALUES (?, ?, ?, ?)
        """, (user_id, label, url, watch_type))
        return cursor.lastrowid


def get_watches(user_id: int) -> list:
    with get_connection() as conn:
        return conn.execute("""
            SELECT * FROM watches
            WHERE user_id = ? AND active = 1
            ORDER BY id ASC
        """, (user_id,)).fetchall()


def get_watch(watch_id: int) -> Optional[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM watches WHERE id = ?", (watch_id,)
        ).fetchone()


def count_watches(user_id: int) -> int:
    with get_connection() as conn:
        row = conn.execute("""
            SELECT COUNT(*) as cnt FROM watches
            WHERE user_id = ? AND active = 1
        """, (user_id,)).fetchone()
        return row["cnt"]


def deactivate_watch(watch_id: int, user_id: int):
    with get_connection() as conn:
        conn.execute("""
            UPDATE watches SET active = 0
            WHERE id = ? AND user_id = ?
        """, (watch_id, user_id))


def update_watch_hash(watch_id: int, new_hash: str):
    with get_connection() as conn:
        conn.execute("""
            UPDATE watches
            SET last_hash    = ?,
                last_checked = datetime('now')
            WHERE id = ?
        """, (new_hash, watch_id))


def update_watch_changed(watch_id: int, new_hash: str):
    with get_connection() as conn:
        conn.execute("""
            UPDATE watches
            SET last_hash    = ?,
                last_checked = datetime('now'),
                last_changed = datetime('now')
            WHERE id = ?
        """, (new_hash, watch_id))


def increment_fetch_failures(watch_id: int) -> int:
    """Track consecutive fetch failures — returns new failure count."""
    with get_connection() as conn:
        conn.execute("""
            UPDATE watches
            SET fetch_failures = COALESCE(fetch_failures, 0) + 1
            WHERE id = ?
        """, (watch_id,))
        row = conn.execute(
            "SELECT fetch_failures FROM watches WHERE id = ?", (watch_id,)
        ).fetchone()
        return row["fetch_failures"] if row else 0


def reset_fetch_failures(watch_id: int):
    with get_connection() as conn:
        conn.execute("""
            UPDATE watches SET fetch_failures = 0 WHERE id = ?
        """, (watch_id,))


def get_all_active_watches() -> list:
    with get_connection() as conn:
        return conn.execute("""
            SELECT w.*, u.tier FROM watches w
            JOIN users u ON w.user_id = u.user_id
            WHERE w.active = 1
        """).fetchall()


def get_watches_due(interval_hrs: int) -> list:
    with get_connection() as conn:
        return conn.execute("""
            SELECT w.*, u.tier FROM watches w
            JOIN users u ON w.user_id = u.user_id
            WHERE w.active = 1
            AND (
                w.last_checked IS NULL
                OR datetime(w.last_checked) <= datetime('now', ? || ' hours')
            )
        """, (f"-{interval_hrs}",)).fetchall()


# ─── Change Log ───────────────────────────────────────────────────────────────

def log_change(
    watch_id:     int,
    user_id:      int,
    old_snapshot: str,
    new_snapshot: str,
    ai_summary:   str = None,
) -> int:
    with get_connection() as conn:
        cursor = conn.execute("""
            INSERT INTO change_log
                (watch_id, user_id, old_snapshot, new_snapshot, ai_summary)
            VALUES (?, ?, ?, ?, ?)
        """, (watch_id, user_id, old_snapshot, new_snapshot, ai_summary))
        return cursor.lastrowid


def mark_notified(change_id: int):
    with get_connection() as conn:
        conn.execute(
            "UPDATE change_log SET notified = 1 WHERE id = ?", (change_id,)
        )


def get_last_snapshot(watch_id: int) -> Optional[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute("""
            SELECT * FROM change_log
            WHERE watch_id = ?
            ORDER BY detected_at DESC
            LIMIT 1
        """, (watch_id,)).fetchone()


def get_recent_changes(user_id: int, limit: int = 10) -> list:
    with get_connection() as conn:
        return conn.execute("""
            SELECT cl.*, w.label, w.url, w.watch_type
            FROM change_log cl
            JOIN watches w ON cl.watch_id = w.id
            WHERE cl.user_id = ?
            ORDER BY cl.detected_at DESC
            LIMIT ?
        """, (user_id, limit)).fetchall()


def get_unnotified_changes() -> list:
    with get_connection() as conn:
        return conn.execute("""
            SELECT cl.*, w.label, w.url, w.watch_type, w.user_id
            FROM change_log cl
            JOIN watches w ON cl.watch_id = w.id
            WHERE cl.notified = 0
            ORDER BY cl.detected_at ASC
        """).fetchall()


# ─── Payments ─────────────────────────────────────────────────────────────────

def log_payment(user_id: int, payment_id: str, amount_usd: float, tier: str):
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO payments (user_id, payment_id, amount_usd, tier)
            VALUES (?, ?, ?, ?)
        """, (user_id, payment_id, amount_usd, tier))


def confirm_payment(payment_id: str):
    with get_connection() as conn:
        conn.execute("""
            UPDATE payments SET status = 'confirmed' WHERE payment_id = ?
        """, (payment_id,))


def get_payment(payment_id: str) -> Optional[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute(
            "SELECT * FROM payments WHERE payment_id = ?", (payment_id,)
        ).fetchone()
        
def get_user_settings(user_id: int) -> Optional[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute("""
            SELECT quiet_hours_on, quiet_hours_start, quiet_hours_end
            FROM users WHERE user_id = ?
        """, (user_id,)).fetchone()


def save_quiet_hours(user_id: int, start_hr: int, end_hr: int):
    with get_connection() as conn:
        conn.execute("""
            UPDATE users
            SET quiet_hours_on    = 1,
                quiet_hours_start = ?,
                quiet_hours_end   = ?
            WHERE user_id = ?
        """, (start_hr, end_hr, user_id))


def toggle_quiet_hours(user_id: int, enabled: bool):
    with get_connection() as conn:
        conn.execute("""
            UPDATE users SET quiet_hours_on = ?
            WHERE user_id = ?
        """, (1 if enabled else 0, user_id))


def clear_quiet_hours(user_id: int):
    with get_connection() as conn:
        conn.execute("""
            UPDATE users
            SET quiet_hours_on    = 0,
                quiet_hours_start = NULL,
                quiet_hours_end   = NULL
            WHERE user_id = ?
        """, (user_id,))
