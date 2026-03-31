# handlers/remove.py
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from database.models import (
    get_user,
    get_watches,
    get_watch,
    deactivate_watch,
)
from config import WATCH_TYPE_LABELS

logger = logging.getLogger(__name__)


def _build_remove_keyboard(watches: list) -> InlineKeyboardMarkup:
    """One button per active watch + a cancel button."""
    buttons = []

    for watch in watches:
        label      = watch["label"]
        watch_type = watch["watch_type"]
        type_icon  = {"page": "📄", "jobs": "💼", "reviews": "⭐"}.get(watch_type, "🔍")
        buttons.append([
            InlineKeyboardButton(
                text          = f"{type_icon} {label}",
                callback_data = f"remove_{watch['id']}",
            )
        ])

    buttons.append([
        InlineKeyboardButton("❌ Cancel", callback_data="remove_cancel")
    ])

    return InlineKeyboardMarkup(buttons)


def _build_confirm_keyboard(watch_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Yes, remove it", callback_data=f"remove_confirm_{watch_id}"),
            InlineKeyboardButton("↩️ Keep it",        callback_data="remove_cancel"),
        ]
    ])


async def remove_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point — /remove command. Show list of watches to remove."""
    user_tg = update.effective_user
    user    = get_user(user_tg.id)

    if not user:
        await _reply(update, "Please send /start first.")
        return

    watches = get_watches(user_tg.id)

    if not watches:
        await _reply(update, (
            "🗑 <b>Remove a Watch</b>\n\n"
            "You have no active watches to remove.\n"
            "Use /watch to add one."
        ))
        return

    keyboard = _build_remove_keyboard(watches)

    await _reply(update, (
        "🗑 <b>Remove a Watch</b>\n\n"
        "Select the competitor you want to stop monitoring:"
    ), keyboard)


async def remove_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle remove button presses — select → confirm → execute."""
    query = update.callback_query
    await query.answer()
    data  = query.data

    # --- Cancel at any point ---
    if data == "remove_cancel":
        await query.message.edit_text(
            text       = "↩️ Cancelled. Your watches are unchanged.",
            parse_mode = ParseMode.HTML,
        )
        return

    # --- Confirm step ---
    if data.startswith("remove_confirm_"):
        watch_id = int(data.replace("remove_confirm_", ""))
        user_id  = update.effective_user.id
        watch    = get_watch(watch_id)

        if not watch or watch["user_id"] != user_id:
            await query.message.edit_text("⚠️ Watch not found or already removed.")
            return

        deactivate_watch(watch_id, user_id)

        await query.message.edit_text(
            text = (
                f"✅ <b>{watch['label']}</b> has been removed.\n\n"
                f"Use /watch to add a new competitor.\n"
                f"Use /list to see remaining watches."
            ),
            parse_mode = ParseMode.HTML,
        )
        return

    # --- Selection step — show confirm prompt ---
    if data.startswith("remove_"):
        watch_id = int(data.replace("remove_", ""))
        user_id  = update.effective_user.id
        watch    = get_watch(watch_id)

        if not watch or watch["user_id"] != user_id:
            await query.message.edit_text("⚠️ Watch not found.")
            return

        type_label = WATCH_TYPE_LABELS.get(watch["watch_type"], watch["watch_type"])

        keyboard = _build_confirm_keyboard(watch_id)

        await query.message.edit_text(
            text = (
                f"🗑 <b>Remove this watch?</b>\n\n"
                f"🏷 <b>Competitor:</b> {watch['label']}\n"
                f"🔗 <b>URL:</b> {watch['url']}\n"
                f"📂 <b>Type:</b> {type_label}\n\n"
                f"This will stop all monitoring and alerts for this competitor."
            ),
            parse_mode   = ParseMode.HTML,
            reply_markup = keyboard,
        )


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
    )
    if update.message:
        await update.message.reply_text(**kwargs)
    elif update.callback_query:
        await update.callback_query.message.reply_text(**kwargs)
