# handlers/digest.py
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from database.models import get_user, get_recent_changes, get_watches
from config import TIER_LABELS, TIER_LIMITS

logger = logging.getLogger(__name__)

# Updated icons to match expanded watch types
WATCH_TYPE_ICONS = {
    "page":      "📄",
    "jobs":      "💼",
    "reviews":   "⭐",
    "pricing":   "💰",
    "changelog": "📝",
}


def _get_icon(watch_type: str) -> str:
    return WATCH_TYPE_ICONS.get(watch_type, "🔍")


def _format_change(change) -> str:
    """
    Format a single change entry for the digest view.
    Shows AI summary if available, otherwise shows
    a meaningful snippet from the snapshot — never empty.
    """
    watch_type = change["watch_type"] if "watch_type" in change.keys() else "page"
    icon       = _get_icon(watch_type)
    label      = change["label"]
    url        = change["url"]
    detected   = change["detected_at"][:16]   # trim seconds
    ai_summary = change["ai_summary"]
    snapshot   = change["new_snapshot"] or ""

    lines = [f"{icon} <b>{label}</b> — <i>{detected}</i>"]

    if ai_summary:
        # AI summary available — show it
        lines.append(f"   🧠 {ai_summary[:250]}")

    elif snapshot:
        # No AI — show a cleaned snippet of what actually changed
        # Strip diff markers for readability
        clean_lines = [
            l.lstrip("+-").strip()
            for l in snapshot.splitlines()
            if l.strip() and not l.startswith(("@@", "---", "+++"))
        ]
        snippet = " · ".join(clean_lines[:3])
        if snippet:
            lines.append(f"   📝 {snippet[:200]}")
        else:
            lines.append(f"   📝 Content changed on this page.")

    else:
        lines.append(f"   📝 Change detected.")

    lines.append(f"   🔗 <a href='{url}'>View page →</a>")

    return "\n".join(lines)


def _build_digest_keyboard(has_changes: bool) -> InlineKeyboardMarkup:
    buttons = [
        [
            InlineKeyboardButton("📋 My Watches", callback_data="list_goto_digest"),
            InlineKeyboardButton("➕ Add Watch",  callback_data="start_watch"),
        ]
    ]
    if has_changes:
        buttons.insert(0, [
            InlineKeyboardButton("🗑 Remove a Watch", callback_data="list_goto_remove"),
        ])
    return InlineKeyboardMarkup(buttons)


def _split_digest(lines: list[str], limit: int = 3800) -> list[str]:
    """
    Split digest into multiple messages if it exceeds Telegram's limit.
    Splits cleanly between change entries.
    """
    chunks  = []
    current = []
    current_len = 0

    for line in lines:
        if current_len + len(line) + 1 > limit:
            chunks.append("\n".join(current))
            current     = [line]
            current_len = len(line)
        else:
            current.append(line)
            current_len += len(line) + 1

    if current:
        chunks.append("\n".join(current))

    return chunks if chunks else ["\n".join(lines)]


async def digest_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_tg = update.effective_user
    user    = get_user(user_tg.id)

    if not user:
        await _reply(update, "Please send /start first.")
        return

    watches = get_watches(user_tg.id)

    if not watches:
        await _reply(update, (
            "📊 <b>Digest</b>\n\n"
            "You have no active watches yet.\n\n"
            "Use /watch to start monitoring competitors."
        ))
        return

    tier       = user["tier"]
    tier_label = TIER_LABELS.get(tier, tier)
    ai_enabled = TIER_LIMITS[tier]["ai_summary"]
    limit      = 20

    changes = get_recent_changes(user_tg.id, limit=limit)

    # ── No changes yet ────────────────────────────────────────────────────────
    if not changes:
        interval = TIER_LIMITS[tier]["check_interval_hrs"]
        await _reply(update, (
            f"📊 <b>Intelligence Digest</b>  <i>({tier_label})</i>\n\n"
            f"✅ No changes detected across your <b>{len(watches)}</b> "
            f"watched competitor(s).\n\n"
            f"Checks run every <b>{interval}h</b> on your plan. "
            f"I'll alert you the moment something changes."
        ))
        return

    # ── Group changes by watch type for better readability ───────────────────
    by_type = {}
    for change in changes:
        wtype = change["watch_type"] if "watch_type" in change.keys() else "page"
        by_type.setdefault(wtype, []).append(change)

    # ── Build digest message ──────────────────────────────────────────────────
    header_lines = [
        f"📊 <b>Intelligence Digest</b>  <i>({tier_label})</i>\n",
        f"<b>{len(changes)}</b> change(s) across "
        f"<b>{len(watches)}</b> watch(es):\n",
    ]

    if not ai_enabled:
        header_lines.append(
            "💡 <i>Upgrade to Pro for AI analysis on every change.</i>\n"
        )

    body_lines = []
    for wtype, wchanges in by_type.items():
        icon  = _get_icon(wtype)
        label = {
            "page":      "Page Changes",
            "jobs":      "Job Posting Changes",
            "reviews":   "Review Changes",
            "pricing":   "Pricing Changes",
            "changelog": "Changelog Updates",
        }.get(wtype, "Changes")

        body_lines.append(f"\n{icon} <b>{label} ({len(wchanges)})</b>")
        body_lines.append("─" * 20)

        for i, change in enumerate(wchanges, start=1):
            body_lines.append(f"{i}. {_format_change(change)}")
            body_lines.append("")

    all_lines = header_lines + body_lines
    chunks    = _split_digest(all_lines)
    keyboard  = _build_digest_keyboard(has_changes=True)

    for i, chunk in enumerate(chunks):
        # Only attach keyboard to last chunk
        kb = keyboard if i == len(chunks) - 1 else None
        await _reply(update, chunk, kb)


# ─── Shared reply helper ──────────────────────────────────────────────────────

async def _reply(
    update: Update,
    text: str,
    keyboard: InlineKeyboardMarkup = None,
):
    kwargs = dict(
        text                     = text,
        parse_mode               = ParseMode.HTML,
        reply_markup             = keyboard,
        disable_web_page_preview = True,
    )
    if update.message:
        await update.message.reply_text(**kwargs)
    elif update.callback_query:
        await update.callback_query.message.reply_text(**kwargs)
