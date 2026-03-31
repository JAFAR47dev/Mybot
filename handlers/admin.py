# handlers/admin.py
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from database.models import (
    get_all_users,
    get_watches,
    set_user_tier,
    get_recent_changes,
)
from services.notifier import send_system_message
from config import ADMIN_IDS, TIER_LABELS, TIER_LIMITS

logger = logging.getLogger(__name__)


def _is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS


def _build_admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("📊 Stats",         callback_data="admin_stats"),
            InlineKeyboardButton("👥 Users",          callback_data="admin_users"),
        ],
        [
            InlineKeyboardButton("📣 Broadcast",      callback_data="admin_broadcast"),
            InlineKeyboardButton("🔧 Set User Tier",  callback_data="admin_set_tier"),
        ],
    ])


async def admin_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point — /admin command."""
    user_id = update.effective_user.id

    if not _is_admin(user_id):
        await update.message.reply_text("⛔ Unauthorized.")
        return

    await update.message.reply_text(
        text         = "🔧 <b>Admin Panel</b>\n\nSelect an action:",
        parse_mode   = ParseMode.HTML,
        reply_markup = _build_admin_keyboard(),
    )


async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query   = update.callback_query
    user_id = update.effective_user.id
    await query.answer()

    if not _is_admin(user_id):
        await query.message.edit_text("⛔ Unauthorized.")
        return

    data = query.data

    # ── Stats ──────────────────────────────────────────────────────────────────
    if data == "admin_stats":
        users   = get_all_users()
        total   = len(users)
        by_tier = {}
        total_watches = 0

        for user in users:
            tier = user["tier"]
            by_tier[tier] = by_tier.get(tier, 0) + 1
            watches = get_watches(user["user_id"])
            total_watches += len(watches)

        tier_lines = "\n".join(
            f"  {TIER_LABELS.get(t, t)}: {count}"
            for t, count in sorted(by_tier.items())
        )

        await query.message.edit_text(
            text = (
                f"📊 <b>Bot Stats</b>\n\n"
                f"👥 Total users: <b>{total}</b>\n"
                f"👁 Total watches: <b>{total_watches}</b>\n\n"
                f"<b>By tier:</b>\n{tier_lines}"
            ),
            parse_mode   = ParseMode.HTML,
            reply_markup = InlineKeyboardMarkup([[
                InlineKeyboardButton("↩️ Back", callback_data="admin_back")
            ]]),
        )

    # ── Users list ─────────────────────────────────────────────────────────────
    elif data == "admin_users":
        users = get_all_users()
        lines = ["👥 <b>All Users</b>\n"]

        for user in users[:30]:   # cap at 30 to avoid message length limit
            watch_count = len(get_watches(user["user_id"]))
            tier_label  = TIER_LABELS.get(user["tier"], user["tier"])
            name        = user["first_name"] or user["username"] or str(user["user_id"])
            lines.append(
                f"• <b>{name}</b> — {tier_label} — "
                f"{watch_count} watch(es) — joined {user['joined_at'][:10]}"
            )

        if len(users) > 30:
            lines.append(f"\n<i>...and {len(users) - 30} more</i>")

        await query.message.edit_text(
            text = "\n".join(lines),
            parse_mode   = ParseMode.HTML,
            reply_markup = InlineKeyboardMarkup([[
                InlineKeyboardButton("↩️ Back", callback_data="admin_back")
            ]]),
        )

    # ── Broadcast ──────────────────────────────────────────────────────────────
    elif data == "admin_broadcast":
        context.user_data["admin_action"] = "broadcast"
        await query.message.edit_text(
            text = (
                "📣 <b>Broadcast Message</b>\n\n"
                "Reply with the message to send to all users.\n"
                "<i>Supports HTML formatting.</i>\n\n"
                "Send /cancel to abort."
            ),
            parse_mode = ParseMode.HTML,
        )

    # ── Set user tier ──────────────────────────────────────────────────────────
    elif data == "admin_set_tier":
        context.user_data["admin_action"] = "set_tier"
        await query.message.edit_text(
            text = (
                "🔧 <b>Set User Tier</b>\n\n"
                "Reply with:\n"
                "<code>USER_ID TIER</code>\n\n"
                "Example: <code>123456789 pro</code>\n\n"
                "Valid tiers: free, starter, pro, agency\n\n"
                "Send /cancel to abort."
            ),
            parse_mode = ParseMode.HTML,
        )

    # ── Back ───────────────────────────────────────────────────────────────────
    elif data == "admin_back":
        await query.message.edit_text(
            text         = "🔧 <b>Admin Panel</b>\n\nSelect an action:",
            parse_mode   = ParseMode.HTML,
            reply_markup = _build_admin_keyboard(),
        )


async def admin_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles free-text replies for broadcast and set_tier admin actions.
    Register this as a MessageHandler in bot.py filtered to ADMIN_IDS.
    """
    user_id = update.effective_user.id

    if not _is_admin(user_id):
        return

    action = context.user_data.get("admin_action")

    if not action:
        return

    text = update.message.text.strip()

    if text == "/cancel":
        context.user_data.pop("admin_action", None)
        await update.message.reply_text("❌ Action cancelled.")
        return

    # ── Broadcast ──────────────────────────────────────────────────────────────
    if action == "broadcast":
        users     = get_all_users()
        bot       = context.bot
        sent      = 0
        failed    = 0

        await update.message.reply_text(
            f"📣 Sending to {len(users)} user(s)..."
        )

        for user in users:
            success = await send_system_message(bot, user["user_id"], text)
            if success:
                sent += 1
            else:
                failed += 1

        context.user_data.pop("admin_action", None)
        await update.message.reply_text(
            f"✅ Broadcast complete.\n"
            f"Sent: <b>{sent}</b> | Failed: <b>{failed}</b>",
            parse_mode = ParseMode.HTML,
        )

    # ── Set tier ───────────────────────────────────────────────────────────────
    elif action == "set_tier":
        parts = text.split()

        if len(parts) != 2:
            await update.message.reply_text(
                "⚠️ Format: <code>USER_ID TIER</code>",
                parse_mode = ParseMode.HTML,
            )
            return

        try:
            target_id   = int(parts[0])
            target_tier = parts[1].lower()
        except ValueError:
            await update.message.reply_text("⚠️ Invalid user ID.")
            return

        if target_tier not in TIER_LIMITS:
            await update.message.reply_text(
                f"⚠️ Invalid tier. Use: {', '.join(TIER_LIMITS.keys())}"
            )
            return

        set_user_tier(target_id, target_tier)
        context.user_data.pop("admin_action", None)

        await update.message.reply_text(
            f"✅ User <code>{target_id}</code> set to "
            f"<b>{TIER_LABELS[target_tier]}</b>.",
            parse_mode = ParseMode.HTML,
        )

        await send_system_message(
            bot     = context.bot,
            user_id = target_id,
            text    = (
                f"🎉 Your plan has been updated to "
                f"<b>{TIER_LABELS[target_tier]}</b>.\n\n"
                f"Enjoy your new features!"
            ),
        )