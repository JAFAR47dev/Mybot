# handlers/start.py
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from database.models import upsert_user, get_user, get_watches, get_recent_changes
from config import TIER_LABELS, TIER_LIMITS

logger = logging.getLogger(__name__)


# ─── Keyboards ────────────────────────────────────────────────────────────────

def _build_new_user_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("➕ Watch My First Competitor", callback_data="start_watch")],
        [InlineKeyboardButton("🚀 See Plans & Pricing",       callback_data="start_upgrade")],
        [InlineKeyboardButton("📖 How It Works",              callback_data="start_help")],
    ])


def _build_free_user_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➕ Add Competitor", callback_data="start_watch"),
            InlineKeyboardButton("📋 My Watches",     callback_data="start_list"),
        ],
        [
            InlineKeyboardButton("📊 Latest Digest",  callback_data="start_digest"),
            InlineKeyboardButton("📖 Help",           callback_data="start_help"),
        ],
        [InlineKeyboardButton("⚡ Upgrade — Get AI Summaries + Instant Alerts", callback_data="start_upgrade")],
    ])


def _build_paid_user_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("➕ Add Competitor", callback_data="start_watch"),
            InlineKeyboardButton("📋 My Watches",     callback_data="start_list"),
        ],
        [
            InlineKeyboardButton("📊 Latest Digest",  callback_data="start_digest"),
            InlineKeyboardButton("📖 Help",           callback_data="start_help"),
        ],
    ])


def _build_keyboard(tier: str, has_watches: bool) -> InlineKeyboardMarkup:
    if not has_watches and tier == "free":
        return _build_new_user_keyboard()
    if tier == "free":
        return _build_free_user_keyboard()
    return _build_paid_user_keyboard()


# ─── Message Builders ─────────────────────────────────────────────────────────

def _new_user_message(first_name: str) -> str:
    return (
        f"👁 <b>Welcome to Competitor Intelligence Bot, {first_name}!</b>\n\n"
        f"I watch your competitors 24/7 and alert you the moment something changes:\n\n"
        f"📄 <b>Pricing pages</b> — know before your customers do\n"
        f"💼 <b>Job postings</b> — spot their next move early\n"
        f"⭐ <b>Reviews</b> — track sentiment shifts on G2, Trustpilot & more\n\n"
        f"Pro users also get <b>AI-powered analysis</b> explaining <i>what the change means</i> "
        f"strategically — not just that it happened.\n\n"
        f"<b>Add your first competitor to get started. It takes 30 seconds.</b>"
    )


def _returning_user_message(user, watches: list, changes: list) -> str:
    tier        = user["tier"]
    tier_label  = TIER_LABELS.get(tier, tier)
    limits      = TIER_LIMITS[tier]
    watch_count = len(watches)
    max_watches = limits["max_watches"]
    interval    = limits["check_interval_hrs"]
    ai_enabled  = limits["ai_summary"]
    first_name  = user["first_name"] or "there"

    # Build activity summary
    if changes:
        recent_count = len(changes)
        last         = changes[0]
        last_label   = last["label"]
        last_time    = last["detected_at"][:16]
        activity = (
            f"🔔 <b>{recent_count} change(s)</b> detected recently\n"
            f"   Latest: <b>{last_label}</b> — <i>{last_time}</i>"
        )
    else:
        activity = "✅ No changes detected — your competitors are quiet"

    # Watches status bar
    slots_used = "█" * watch_count + "░" * (max_watches - watch_count)
    slots_line = f"[{slots_used}] {watch_count}/{max_watches}"

    # Upgrade nudge for free users watching competitors actively
    nudge = ""
    if tier == "free" and watch_count > 0 and not ai_enabled:
        nudge = (
            "\n\n💡 <i>Upgrade to Pro to get AI analysis explaining "
            "what each change means for your business.</i>"
        )

    return (
        f"👋 <b>Welcome back, {first_name}!</b>\n\n"
        f"Plan: {tier_label}  |  "
        f"Checks: every <b>{interval}h</b>  |  "
        f"AI: <b>{'✅' if ai_enabled else '❌'}</b>\n\n"
        f"Watches: <code>{slots_line}</code>\n\n"
        f"{activity}"
        f"{nudge}"
    )


def _active_user_with_changes_message(user, watches: list, changes: list) -> str:
    """
    Special message variant for users who have recent changes —
    surfaces the intelligence immediately on open.
    """
    tier       = user["tier"]
    ai_enabled = TIER_LIMITS[tier]["ai_summary"]
    first_name = user["first_name"] or "there"

    lines = [f"👁 <b>Intelligence Update, {first_name}</b>\n"]

    for change in changes[:3]:
        label      = change["label"]
        watch_type = change["watch_type"]
        detected   = change["detected_at"][:16]
        ai_summary = change["ai_summary"]

        icon = {"page": "📄", "jobs": "💼", "reviews": "⭐"}.get(watch_type, "🔍")
        lines.append(f"{icon} <b>{label}</b> — <i>{detected}</i>")

        if ai_summary and ai_enabled:
            lines.append(f"   🧠 {ai_summary[:180]}")
        else:
            lines.append(f"   Change detected. Tap <b>Latest Digest</b> for details.")

        lines.append("")

    if len(changes) > 3:
        lines.append(f"<i>+{len(changes) - 3} more change(s) in your digest.</i>\n")

    return "\n".join(lines)


# ─── Main Handler ─────────────────────────────────────────────────────────────

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_tg = update.effective_user

    upsert_user(
        user_id    = user_tg.id,
        username   = user_tg.username   or "",
        first_name = user_tg.first_name or "",
    )

    user       = get_user(user_tg.id)
    watches    = get_watches(user_tg.id)
    changes    = get_recent_changes(user_tg.id, limit=5)
    tier       = user["tier"]
    has_watches = len(watches) > 0
    has_changes = len(changes) > 0

    # Choose message variant
    if not has_watches:
        message = _new_user_message(user_tg.first_name or "there")
    elif has_changes:
        message = _active_user_with_changes_message(user, watches, changes)
    else:
        message = _returning_user_message(user, watches, changes)

    keyboard = _build_keyboard(tier, has_watches)

    await update.message.reply_text(
        text                     = message,
        parse_mode               = ParseMode.HTML,
        reply_markup             = keyboard,
        disable_web_page_preview = True,
    )


# ─── Callback Handler ─────────────────────────────────────────────────────────

async def start_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data  = query.data

    if data == "start_watch":
        # Handled by ConversationHandler — should not reach here
        # but kept as safety fallback
        await query.message.reply_text(
            "➕ Use /watch to add a competitor.",
            parse_mode = ParseMode.HTML,
        )

    elif data == "start_list":
        from handlers.list import list_handler
        await list_handler(update, context)

    elif data == "start_digest":
        from handlers.digest import digest_handler
        await digest_handler(update, context)

    elif data == "start_upgrade":
        from handlers.upgrade import upgrade_handler
        await upgrade_handler(update, context)

    elif data == "start_help":
        await query.message.reply_text(
            text = (
                "📖 <b>How It Works</b>\n\n"
                "1️⃣ <b>/watch</b> — Add a competitor URL to monitor\n"
                "2️⃣ I check it automatically based on your plan interval\n"
                "3️⃣ The moment something changes, I alert you instantly\n"
                "4️⃣ Pro users get an AI explanation of what the change means\n\n"
                "<b>Commands</b>\n"
                "/watch   — Add a competitor\n"
                "/list    — View active watches\n"
                "/remove  — Stop watching a competitor\n"
                "/digest  — View recent changes\n"
                "/upgrade — Upgrade your plan\n"
                "/start   — Back to main menu\n\n"
                "<b>Supported watch types:</b>\n"
                "📄 Pricing & landing pages\n"
                "💼 Job postings (Greenhouse, Lever + more)\n"
                "⭐ Reviews (G2, Trustpilot, Capterra, ProductHunt)\n"
            ),
            parse_mode               = ParseMode.HTML,
            disable_web_page_preview = True,
        )
