# handlers/watch.py
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from telegram.constants import ParseMode

from database.models import get_user, upsert_user, add_watch, count_watches
from config import TIER_LIMITS, WATCH_TYPE_LABELS
from utils.helpers import is_valid_url, normalize_url

logger = logging.getLogger(__name__)

WATCH_LABEL, WATCH_URL, WATCH_TYPE = range(3)

# Expanded watch type labels
EXPANDED_WATCH_TYPE_LABELS = {
    "page":      "📄 Pricing / Landing Page",
    "jobs":      "💼 Job Postings",
    "reviews":   "⭐ Reviews (G2 / Trustpilot)",
    "pricing":   "💰 Pricing Page (deep scan)",
    "changelog": "📝 Changelog / Blog",
}


def _type_keyboard() -> InlineKeyboardMarkup:
    buttons = [
        [InlineKeyboardButton(label, callback_data=f"watch_type_{key}")]
        for key, label in EXPANDED_WATCH_TYPE_LABELS.items()
    ]
    buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="watch_type_cancel")])
    return InlineKeyboardMarkup(buttons)


async def watch_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_tg = update.effective_user
    upsert_user(user_tg.id, user_tg.username or "", user_tg.first_name or "")

    user        = get_user(user_tg.id)
    tier        = user["tier"]
    watch_count = count_watches(user_tg.id)
    max_watches = TIER_LIMITS[tier]["max_watches"]

    if watch_count >= max_watches:
        await _reply(update, (
            f"⚠️ You've reached your watch limit "
            f"(<b>{watch_count}/{max_watches}</b> on {tier} plan).\n\n"
            f"Use /upgrade to monitor more competitors."
        ))
        return ConversationHandler.END

    await _reply(update, (
        "➕ <b>Add a Competitor Watch</b>\n\n"
        "Step 1 of 3 — What's the competitor's name?\n"
        "<i>e.g. Notion, Linear, Stripe</i>"
    ))
    return WATCH_LABEL


async def watch_label_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    label = update.message.text.strip()

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
            f"<b>Examples:</b>\n"
            f"  • https://notion.so/pricing\n"
            f"  • https://linear.app/changelog\n"
            f"  • https://boards.greenhouse.io/stripe\n"
            f"  • https://www.g2.com/products/notion/reviews"
        ),
        parse_mode = ParseMode.HTML,
    )
    return WATCH_URL


async def watch_url_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

    # Normalize before storing
    url = normalize_url(url)
    context.user_data["watch_url"] = url

    # Auto-suggest watch type based on URL
    suggested = _suggest_watch_type(url)
    context.user_data["suggested_type"] = suggested

    suggestion_text = ""
    if suggested:
        label = EXPANDED_WATCH_TYPE_LABELS.get(suggested, suggested)
        suggestion_text = f"\n\n💡 <i>Tip: Based on your URL, <b>{label}</b> is recommended.</i>"

    await update.message.reply_text(
        text = (
            f"✅ URL saved.\n\n"
            f"Step 3 of 3 — What should I watch for?"
            f"{suggestion_text}"
        ),
        parse_mode   = ParseMode.HTML,
        reply_markup = _type_keyboard(),
    )
    return WATCH_TYPE


def _suggest_watch_type(url: str) -> str | None:
    """Suggest a watch type based on URL patterns."""
    url_lower = url.lower()
    if any(kw in url_lower for kw in ["greenhouse.io", "lever.co", "jobs", "careers", "hiring"]):
        return "jobs"
    if any(kw in url_lower for kw in ["g2.com", "trustpilot", "capterra", "reviews"]):
        return "reviews"
    if any(kw in url_lower for kw in ["pricing", "plans", "price"]):
        return "pricing"
    if any(kw in url_lower for kw in ["changelog", "blog", "releases", "updates", "news"]):
        return "changelog"
    return "page"


async def watch_type_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "watch_type_cancel":
        context.user_data.clear()
        await query.message.reply_text("❌ Watch cancelled.")
        return ConversationHandler.END

    watch_type = query.data.replace("watch_type_", "")
    label      = context.user_data.get("watch_label", "Unknown")
    url        = context.user_data.get("watch_url", "")
    user_id    = update.effective_user.id

    add_watch(
        user_id    = user_id,
        label      = label,
        url        = url,
        watch_type = watch_type,
    )

    type_label = EXPANDED_WATCH_TYPE_LABELS.get(watch_type, watch_type)
    context.user_data.clear()

    await query.message.reply_text(
        text = (
            f"✅ <b>Watch added!</b>\n\n"
            f"🏷 <b>Competitor:</b> {label}\n"
            f"🔗 <b>URL:</b> {url}\n"
            f"📂 <b>Watching:</b> {type_label}\n\n"
            f"I'll check it on your plan's schedule and alert you "
            f"the moment something changes.\n\n"
            f"Use /list to see all your watches."
        ),
        parse_mode = ParseMode.HTML,
    )
    return ConversationHandler.END


async def _reply(update: Update, text: str):
    if update.message:
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)
    elif update.callback_query:
        await update.callback_query.message.reply_text(text, parse_mode=ParseMode.HTML)
