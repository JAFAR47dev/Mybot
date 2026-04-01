# handlers/start.py
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.constants import ParseMode

from database.models import (
    upsert_user,
    get_user,
    get_watches,
    get_recent_changes,
    add_watch,
)
from config import TIER_LABELS, TIER_LIMITS

logger = logging.getLogger(__name__)

WATCH_TYPE_ICONS = {
    "page":      "📄",
    "jobs":      "💼",
    "reviews":   "⭐",
    "pricing":   "💰",
    "changelog": "📝",
}

DEMO_WATCHES = [
    {
        "label":      "Hacker News",
        "url":        "https://news.ycombinator.com",
        "watch_type": "page",
    },
]


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
        [InlineKeyboardButton(
            "⚡ Upgrade — Get AI Summaries + Instant Alerts",
            callback_data = "start_upgrade",
        )],
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
        f"👁 <b>Welcome to RivalWatch, {first_name}!</b>\n\n"
        f"I watch your competitors 24/7 and alert you the moment "
        f"something changes:\n\n"
        f"📄 <b>Pricing pages</b> — know before your customers do\n"
        f"💼 <b>Job postings</b> — spot their next move early\n"
        f"⭐ <b>Reviews</b> — track sentiment on G2, Trustpilot & more\n"
        f"💰 <b>Pricing deep scan</b> — catch plan restructures instantly\n"
        f"📝 <b>Changelogs</b> — know what they're shipping\n\n"
        f"🎁 <b>I've added Hacker News as your first demo watch.</b>\n"
        f"It updates every few hours — you'll see your first real "
        f"alert soon. That's exactly what a competitor alert looks like.\n\n"
        f"<b>Now add your first real competitor below. "
        f"It takes 30 seconds.</b>"
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

    if changes:
        recent_count = len(changes)
        last         = changes[0]
        activity = (
            f"🔔 <b>{recent_count} change(s)</b> detected recently\n"
            f"   Latest: <b>{last['label']}</b> — "
            f"<i>{last['detected_at'][:16]}</i>"
        )
    else:
        activity = "✅ No changes detected — your competitors are quiet"

    # Visual slots bar
    filled    = "█" * watch_count
    empty     = "░" * max(0, max_watches - watch_count)
    slots_line = f"[{filled}{empty}] {watch_count}/{max_watches}"

    nudge = ""
    if tier == "free" and watch_count > 0 and not ai_enabled:
        nudge = (
            "\n\n💡 <i>Upgrade to Pro to get AI analysis explaining "
            "what each change means strategically.</i>"
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
    tier       = user["tier"]
    ai_enabled = TIER_LIMITS[tier]["ai_summary"]
    first_name = user["first_name"] or "there"

    lines = [f"👁 <b>Intelligence Update, {first_name}</b>\n"]

    for change in changes[:3]:
        label      = change["label"]
        watch_type = change["watch_type"] if "watch_type" in change.keys() else "page"
        detected   = change["detected_at"][:16]
        ai_summary = change["ai_summary"]
        icon       = WATCH_TYPE_ICONS.get(watch_type, "🔍")

        lines.append(f"{icon} <b>{label}</b> — <i>{detected}</i>")

        if ai_summary and ai_enabled:
            lines.append(f"   🧠 {ai_summary[:180]}")
        else:
            # Show a cleaned snippet instead of generic text
            snapshot = change["new_snapshot"] or ""
            clean    = " · ".join([
                l.lstrip("+-").strip()
                for l in snapshot.splitlines()
                if l.strip() and not l.startswith(("@@", "---", "+++"))
            ][:2])
            if clean:
                lines.append(f"   📝 {clean[:150]}")
            else:
                lines.append(f"   📝 Tap <b>Latest Digest</b> for details.")

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

    user        = get_user(user_tg.id)
    watches     = get_watches(user_tg.id)
    is_new_user = len(watches) == 0

    # ── Auto-add demo watch for brand new users ───────────────────────────────
    if is_new_user:
        for demo in DEMO_WATCHES:
            add_watch(
                user_id    = user_tg.id,
                label      = demo["label"],
                url        = demo["url"],
                watch_type = demo["watch_type"],
            )
        watches = get_watches(user_tg.id)

    changes     = get_recent_changes(user_tg.id, limit=5)
    tier        = user["tier"]
    has_watches = len(watches) > 0
    has_changes = len(changes) > 0

    # ── Choose message variant ────────────────────────────────────────────────
    if is_new_user:
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
        # ConversationHandler handles this — fallback only
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
                "2️⃣ I check it automatically on your plan's schedule\n"
                "3️⃣ The moment something changes, I alert you instantly\n"
                "4️⃣ Pro users get AI analysis explaining what it means\n\n"
                "<b>Commands</b>\n"
                "/watch   — Add a competitor\n"
                "/list    — View active watches\n"
                "/remove  — Stop watching a competitor\n"
                "/digest  — View recent changes\n"
                "/upgrade — Upgrade your plan\n"
                "/start   — Back to main menu\n\n"
                "<b>What I can monitor:</b>\n"
                "📄 Pricing & landing pages\n"
                "💰 Pricing pages (deep scan — plan names, prices, CTAs)\n"
                "💼 Job postings (Greenhouse, Lever + generic boards)\n"
                "⭐ Reviews (G2, Trustpilot, Capterra, ProductHunt)\n"
                "📝 Changelogs, blogs & release notes\n\n"
                "<b>Demo watch:</b>\n"
                "Every new user gets <b>Hacker News</b> added automatically "
                "so you see your first real alert within hours of signing up."
            ),
            parse_mode               = ParseMode.HTML,
            disable_web_page_preview = True,
        )
