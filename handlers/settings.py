# handlers/settings.py
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from database.models import (
    get_user,
    get_user_settings,
    save_quiet_hours,
    toggle_quiet_hours,
    clear_quiet_hours,
)

logger = logging.getLogger(__name__)

# Preset quiet hour ranges shown to user
QUIET_PRESETS = {
    "qh_preset_night":    (22, 7,  "🌙 Night   (10pm – 7am)"),
    "qh_preset_late":     (23, 8,  "🌛 Late    (11pm – 8am)"),
    "qh_preset_midnight": (0,  8,  "🕛 Midnight (12am – 8am)"),
    "qh_preset_evening":  (20, 8,  "🌆 Evening  (8pm – 8am)"),
}


def _status_line(settings) -> str:
    if not settings["quiet_hours_on"]:
        return "🔔 Quiet hours: <b>Off</b>"
    start = settings["quiet_hours_start"]
    end   = settings["quiet_hours_end"]
    if start is None or end is None:
        return "🔔 Quiet hours: <b>Off</b>"
    return (
        f"🔕 Quiet hours: <b>On</b> — "
        f"{start:02d}:00 → {end:02d}:00 UTC"
    )


def _build_settings_keyboard(settings) -> InlineKeyboardMarkup:
    is_on = bool(settings["quiet_hours_on"])

    buttons = []

    if is_on:
        buttons.append([
            InlineKeyboardButton(
                "🔔 Turn Off Quiet Hours",
                callback_data = "qh_disable",
            )
        ])
        buttons.append([
            InlineKeyboardButton(
                "🕐 Change Schedule",
                callback_data = "qh_change",
            )
        ])
    else:
        buttons.append([
            InlineKeyboardButton(
                "🔕 Set Quiet Hours",
                callback_data = "qh_set",
            )
        ])

    buttons.append([
        InlineKeyboardButton("❌ Close", callback_data="qh_close")
    ])

    return InlineKeyboardMarkup(buttons)


def _build_preset_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(label, callback_data=key)]
        for key, (_, _, label) in QUIET_PRESETS.items()
    ]
    buttons.append([
        InlineKeyboardButton("↩️ Back", callback_data="qh_back")
    ])
    return InlineKeyboardMarkup(buttons)


def _settings_message(settings) -> str:
    return (
        f"⚙️ <b>Settings</b>\n\n"
        f"{_status_line(settings)}\n\n"
        f"<i>During quiet hours, alerts are held and delivered "
        f"automatically once quiet hours end.\n"
        f"All times are in UTC.</i>"
    )


async def settings_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point — /settings command."""
    user_tg  = update.effective_user
    settings = get_user_settings(user_tg.id)

    if not settings:
        await _reply(update, "Please send /start first.")
        return

    await _reply(
        update,
        _settings_message(settings),
        _build_settings_keyboard(settings),
    )


async def settings_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data    = query.data
    user_id = update.effective_user.id

    # ── Close ─────────────────────────────────────────────────────────────────
    if data == "qh_close":
        await query.message.edit_text(
            "⚙️ Settings closed.",
            parse_mode = ParseMode.HTML,
        )
        return

    # ── Back to main settings ─────────────────────────────────────────────────
    if data == "qh_back":
        settings = get_user_settings(user_id)
        await query.message.edit_text(
            text         = _settings_message(settings),
            parse_mode   = ParseMode.HTML,
            reply_markup = _build_settings_keyboard(settings),
        )
        return

    # ── Show preset picker ────────────────────────────────────────────────────
    if data in ("qh_set", "qh_change"):
        await query.message.edit_text(
            text = (
                "🔕 <b>Choose a Quiet Hours Schedule</b>\n\n"
                "Alerts detected during this window will be "
                "held and delivered when quiet hours end.\n\n"
                "<i>All times are UTC.</i>"
            ),
            parse_mode   = ParseMode.HTML,
            reply_markup = _build_preset_keyboard(),
        )
        return

    # ── Preset selected ───────────────────────────────────────────────────────
    if data in QUIET_PRESETS:
        start_hr, end_hr, label = QUIET_PRESETS[data]
        save_quiet_hours(user_id, start_hr, end_hr)

        settings = get_user_settings(user_id)
        await query.message.edit_text(
            text = (
                f"✅ <b>Quiet hours set!</b>\n\n"
                f"{label}\n\n"
                f"Alerts during this window will be held "
                f"and delivered automatically when quiet hours end.\n\n"
                f"<i>All times are UTC.</i>"
            ),
            parse_mode   = ParseMode.HTML,
            reply_markup = _build_settings_keyboard(settings),
        )
        return

    # ── Disable quiet hours ───────────────────────────────────────────────────
    if data == "qh_disable":
        clear_quiet_hours(user_id)
        settings = get_user_settings(user_id)

        await query.message.edit_text(
            text = (
                f"🔔 <b>Quiet hours disabled.</b>\n\n"
                f"You'll now receive alerts at any time.\n\n"
                f"{_settings_message(settings)}"
            ),
            parse_mode   = ParseMode.HTML,
            reply_markup = _build_settings_keyboard(settings),
        )
        return


# ─── Shared reply helper ──────────────────────────────────────────────────────

async def _reply(
    update: Update,
    text: str,
    keyboard: InlineKeyboardMarkup = None,
):
    kwargs = dict(
        text       = text,
        parse_mode = ParseMode.HTML,
        reply_markup = keyboard,
    )
    if update.message:
        await update.message.reply_text(**kwargs)
    elif update.callback_query:
        await update.callback_query.message.reply_text(**kwargs)
