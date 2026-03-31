# handlers/list.py
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from database.models import get_user, get_watches, get_recent_changes
from config import TIER_LIMITS, WATCH_TYPE_LABELS, TIER_LABELS

logger = logging.getLogger(__name__)


def _format_watch_row(watch, recent_changes: list) -> str:
    """Format a single watch entry for the list view."""
    watch_id   = watch["id"]
    label      = watch["label"]
    url        = watch["url"]
    watch_type = watch["watch_type"]
    last_checked = watch["last_checked"] or "Never"
    last_changed = watch["last_changed"] or "No changes yet"

    type_label = WATCH_TYPE_LABELS.get(watch_type, watch_type)

    # Count changes for this watch
    change_count = sum(1 for c in recent_changes if c["watch_id"] == watch_id)
    change_str   = f"{change_count} recent change(s)" if change_count else "No recent changes"

    return (
        f"<b>{label}</b>\n"
        f"  📂 {type_label}\n"
        f"  🔗 {url[:50]}{'...' if len(url) > 50 else ''}\n"
        f"  🕐 Checked: <i>{last_checked}</i>\n"
        f"  🔔 Last change: <i>{last_changed}</i>\n"
        f"  📊 {change_str}"
    )


def _build_list_keyboard(watches: list) -> InlineKeyboardMarkup:
    """Quick action buttons below the list."""
    buttons = []

    if watches:
        buttons.append([
            InlineKeyboardButton("🗑 Remove a Watch", callback_data="list_goto_remove"),
            InlineKeyboardButton("📊 Get Digest",     callback_data="list_goto_digest"),
        ])

    buttons.append([
        InlineKeyboardButton("➕ Add Watch", callback_data="start_watch"),
    ])

    return InlineKeyboardMarkup(buttons)


async def list_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_tg = update.effective_user
    user    = get_user(user_tg.id)

    if not user:
        await _reply(update, "Please send /start first.")
        return

    watches        = get_watches(user_tg.id)
    recent_changes = get_recent_changes(user_tg.id, limit=50)
    tier           = user["tier"]
    limits         = TIER_LIMITS[tier]
    tier_label     = TIER_LABELS.get(tier, tier)

    if not watches:
        await _reply(update, (
            "📋 <b>My Watches</b>\n\n"
            "You're not watching any competitors yet.\n\n"
            "Use /watch to add your first one."
        ))
        return

    lines = [
        f"📋 <b>My Watches</b>  "
        f"<i>({len(watches)}/{limits['max_watches']} — {tier_label})</i>\n"
    ]

    for i, watch in enumerate(watches, start=1):
        lines.append(f"{i}. {_format_watch_row(watch, recent_changes)}")
        if i < len(watches):
            lines.append("")   # spacer between entries

    message  = "\n".join(lines)
    keyboard = _build_list_keyboard(watches)

    await _reply(update, message, keyboard)


async def list_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle quick action buttons from the list view."""
    query = update.callback_query
    await query.answer()
    data  = query.data

    if data == "list_goto_remove":
        from handlers.remove import remove_handler
        await remove_handler(update, context)

    elif data == "list_goto_digest":
        from handlers.digest import digest_handler
        await digest_handler(update, context)


# ─── Shared reply helper ──────────────────────────────────────────────────────

async def _reply(
    update: Update,
    text: str,
    keyboard: InlineKeyboardMarkup = None,
):
    kwargs = dict(
        text         = text,
        parse_mode   = ParseMode.HTML,
        reply_markup = keyboard,
        disable_web_page_preview = True,
    )
    if update.message:
        await update.message.reply_text(**kwargs)
    elif update.callback_query:
        await update.callback_query.message.reply_text(**kwargs)
