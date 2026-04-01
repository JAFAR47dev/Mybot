# handlers/watch.py
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from telegram.constants import ParseMode

from database.models import get_user, upsert_user, add_watch, count_watches
from config import TIER_LIMITS
from utils.helpers import is_valid_url, normalize_url

logger = logging.getLogger(__name__)

WATCH_LABEL, WATCH_URL, WATCH_TYPE = range(3)

EXPANDED_WATCH_TYPE_LABELS = {
    "page":      "📄 Pricing / Landing Page",
    "pricing":   "💰 Pricing Page (deep scan)",
    "jobs":      "💼 Job Postings",
    "reviews":   "⭐ Reviews (G2 / Trustpilot)",
    "changelog": "📝 Changelog / Blog",
}

# URL keyword → suggested watch type
URL_TYPE_HINTS = [
    (["greenhouse.io", "lever.co", "/jobs", "/careers", "/hiring", "workable"], "jobs"),
    (["g2.com", "trustpilot", "capterra", "producthunt", "/reviews"],           "reviews"),
    (["changelog", "/releases", "/updates", "/whats-new", "release-notes"],     "changelog"),
    (["blog", "/news", "/articles", "/posts"],                                  "changelog"),
    (["/pricing", "/plans", "/price", "price-page"],                            "pricing"),
]


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _suggest_watch_type(url: str) -> str:
    url_lower = url.lower()
    for keywords, watch_type in URL_TYPE_HINTS:
        if any(kw in url_lower for kw in keywords):
            return watch_type
    return "page"


def _type_keyboard(suggested: str = None) -> InlineKeyboardMarkup:
    buttons = []
    for key, label in EXPANDED_WATCH_TYPE_LABELS.items():
        # Add ✨ marker to suggested type so it stands out
        display = f"✨ {label} (recommended)" if key == suggested else label
        buttons.append([InlineKeyboardButton(display, callback_data=f"watch_type_{key}")])
    buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="watch_type_cancel")])
    return InlineKeyboardMarkup(buttons)


async def _reply(update: Update, text: str, keyboard: InlineKeyboardMarkup = None):
    kwargs = dict(text=text, parse_mode=ParseMode.HTML, reply_markup=keyboard)
    if update.message:
        await update.message.reply_text(**kwargs)
    elif update.callback_query:
        await update.callback_query.message.reply_text(**kwargs)


# ─── Conversation steps ───────────────────────────────────────────────────────

async def watch_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point — /watch command."""
    user_tg = update.effective_user
    upsert_user(user_tg.id, user_tg.username or "", user_tg.first_name or "")

    user        = get_user(user_tg.id)
    tier        = user["tier"]
    watch_count = count_watches(user_tg.id)
    max_watches = TIER_LIMITS[tier]["max_watches"]
    remaining   = max_watches - watch_count

    if watch_count >= max_watches:
        await _reply(update, (
            f"⚠️ <b>Watch limit reached</b> "
            f"({watch_count}/{max_watches} on {tier} plan).\n\n"
            f"Use /upgrade to monitor more competitors."
        ))
        return ConversationHandler.END

    await _reply(update, (
        f"➕ <b>Add a Competitor Watch</b>\n\n"
        f"Step 1 of 3 — What's the competitor's name?\n"
        f"<i>e.g. Notion, Linear, Stripe</i>\n\n"
        f"<i>You have {remaining} watch slot(s) remaining.</i>"
    ))
    return WATCH_LABEL


async def watch_label_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Step 2 — store label, ask for URL."""
    label = update.message.text.strip()

    if len(label) < 1:
        await update.message.reply_text(
            "⚠️ Please enter a name for this competitor.",
            parse_mode = ParseMode.HTML,
        )
        return WATCH_LABEL

    if len(label) > 50:
        await update.message.reply_text(
            "⚠️ Name too long. Keep it under 50 characters.",
            parse_mode = ParseMode.HTML,
        )
        return WATCH_LABEL

    context.user_data["watch_label"] = label

    await update.message.reply_text(
        text = (
            f"✅ Got it — <b>{label}</b>\n\n"
            f"Step 2 of 3 — What URL should I monitor?\n\n"
            f"<b>Examples by type:</b>\n"
            f"  💰 https://notion.so/pricing\n"
            f"  📝 https://linear.app/changelog\n"
            f"  💼 https://boards.greenhouse.io/stripe\n"
            f"  ⭐ https://www.g2.com/products/notion/reviews\n"
            f"  📄 https://notion.so"
        ),
        parse_mode = ParseMode.HTML,
    )
    return WATCH_URL


async def watch_url_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Step 3 — validate URL, suggest type, show type picker."""
    url = update.message.text.strip()

    # Auto-add https if missing
    if not url.startswith("http"):
        url = "https://" + url

    if not is_valid_url(url):
        await update.message.reply_text(
            "⚠️ That doesn't look like a valid URL. Please try again.\n"
            "<i>Example: https://notion.so/pricing</i>",
            parse_mode = ParseMode.HTML,
        )
        return WATCH_URL

    url       = normalize_url(url)
    suggested = _suggest_watch_type(url)

    context.user_data["watch_url"]       = url
    context.user_data["suggested_type"]  = suggested

    suggested_label = EXPANDED_WATCH_TYPE_LABELS.get(suggested, suggested)

    await update.message.reply_text(
        text = (
            f"✅ URL saved.\n\n"
            f"Step 3 of 3 — What should I watch for?\n\n"
            f"💡 <i>Based on your URL, "
            f"<b>{suggested_label}</b> is recommended.</i>"
        ),
        parse_mode   = ParseMode.HTML,
        reply_markup = _type_keyboard(suggested=suggested),
    )
    return WATCH_TYPE


async def watch_type_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Final step — save watch to DB and confirm."""
    query = update.callback_query
    await query.answer()

    if query.data == "watch_type_cancel":
        context.user_data.clear()
        await query.message.reply_text(
            "❌ Watch cancelled. Use /watch to start again.",
            parse_mode = ParseMode.HTML,
        )
        return ConversationHandler.END

    watch_type = query.data.replace("watch_type_", "")

    # Validate watch_type is known
    if watch_type not in EXPANDED_WATCH_TYPE_LABELS:
        await query.message.reply_text("⚠️ Invalid selection. Please try /watch again.")
        return ConversationHandler.END

    label   = context.user_data.get("watch_label", "Unknown")
    url     = context.user_data.get("watch_url", "")
    user_id = update.effective_user.id

    if not url:
        await query.message.reply_text("⚠️ Something went wrong. Please try /watch again.")
        context.user_data.clear()
        return ConversationHandler.END

    add_watch(
        user_id    = user_id,
        label      = label,
        url        = url,
        watch_type = watch_type,
    )

    type_label = EXPANDED_WATCH_TYPE_LABELS[watch_type]
    user       = get_user(user_id)
    tier       = user["tier"]
    interval   = TIER_LIMITS[tier]["check_interval_hrs"]
    context.user_data.clear()

    await query.message.reply_text(
        text = (
            f"✅ <b>Watch added!</b>\n\n"
            f"🏷 <b>Competitor:</b> {label}\n"
            f"🔗 <b>URL:</b> {url}\n"
            f"📂 <b>Watching:</b> {type_label}\n"
            f"⏱ <b>Check interval:</b> every {interval}h\n\n"
            f"I'll alert you the moment something changes.\n\n"
            f"Use /list to see all your watches.\n"
            f"Use /watch to add another competitor."
        ),
        parse_mode = ParseMode.HTML,
    )
    return ConversationHandler.END
