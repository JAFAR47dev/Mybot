# services/notifier.py
import logging
from telegram import Bot
from telegram.error import TelegramError
from telegram.constants import ParseMode

from config import TIER_LIMITS, WATCH_TYPE_LABELS

logger    = logging.getLogger(__name__)
TG_LIMIT  = 4000   # Telegram max is 4096 — stay safely under


def _watch_type_icon(watch_type: str) -> str:
    return {
        "page":      "📄",
        "jobs":      "💼",
        "reviews":   "⭐",
        "changelog": "📝",
        "pricing":   "💰",
    }.get(watch_type, "🔍")


def _format_alert(
    label:         str,
    url:           str,
    watch_type:    str,
    summary_diff:  str,
    ai_summary:    str | None,
    price_hits:    list[str] | None,
    link_diff:     dict | None,
    meta_changes:  list[str] | None = None,
    price_changes: list[str] | None = None,
) -> str:
    icon       = _watch_type_icon(watch_type)
    type_label = WATCH_TYPE_LABELS.get(watch_type, watch_type)

    lines = [
        f"{icon} <b>Change Detected — {label}</b>",
        f"",
        f"🔗 <b>URL:</b> {url}",
        f"📂 <b>Type:</b> {type_label}",
        f"",
    ]

    # --- Pricing changes (structured) ---
    if price_changes:
        lines.append("💰 <b>Pricing changes:</b>")
        for change in price_changes[:5]:
            lines.append(f"  • {change}")
        lines.append("")

    # --- Meta changes ---
    if meta_changes:
        lines.append("🏷 <b>Page headline/description changes:</b>")
        for change in meta_changes[:3]:
            lines.append(f"  • {change}")
        lines.append("")

    # --- Jobs: added/removed postings ---
    if watch_type == "jobs" and link_diff:
        added   = link_diff.get("added", [])
        removed = link_diff.get("removed", [])
        if added:
            lines.append(f"✅ <b>New postings ({len(added)}):</b>")
            for item in added[:5]:
                lines.append(f"  • {item[:80]}")
        if removed:
            lines.append(f"❌ <b>Removed postings ({len(removed)}):</b>")
            for item in removed[:5]:
                lines.append(f"  • {item[:80]}")
        lines.append("")

    # --- Generic diff ---
    elif summary_diff and not price_changes and not meta_changes:
        lines.append("📝 <b>What changed:</b>")
        lines.append(f"<pre>{summary_diff[:400]}</pre>")
        lines.append("")

    # --- AI analysis (Pro/Agency) ---
    if ai_summary:
        lines.append("🧠 <b>AI Take:</b>")
        lines.append(ai_summary[:500])
        lines.append("")

    lines.append(f'<a href="{url}">View page →</a>')
    return "\n".join(lines)


def _split_message(text: str, limit: int = TG_LIMIT) -> list[str]:
    """
    Split a message into chunks that fit Telegram's limit.
    Splits on newlines to avoid breaking mid-sentence.
    """
    if len(text) <= limit:
        return [text]

    chunks = []
    current = []
    current_len = 0

    for line in text.splitlines(keepends=True):
        if current_len + len(line) > limit:
            chunks.append("".join(current))
            current     = [line]
            current_len = len(line)
        else:
            current.append(line)
            current_len += len(line)

    if current:
        chunks.append("".join(current))

    return chunks


from utils.helpers import is_quiet_hours
from database.models import get_user


async def send_alert(
    bot:           Bot,
    user_id:       int,
    label:         str,
    url:           str,
    watch_type:    str,
    summary_diff:  str,
    ai_summary:    str | None = None,
    price_hits:    list[str] | None = None,
    link_diff:     dict | None = None,
    meta_changes:  list[str] | None = None,
    price_changes: list[str] | None = None,
) -> bool:
    """
    Format and send a change alert.
    Skips delivery silently if user is in quiet hours —
    change is already logged in DB so retry job will
    deliver it when quiet hours end.
    """
    # Check quiet hours before sending
    user = get_user(user_id)
    if user and is_quiet_hours(user):
        logger.info(
            f"[notifier] Quiet hours active for user={user_id} — "
            f"alert for {label} held, will retry later"
        )
        return False   # returning False keeps change as unnotified in DB

    message = _format_alert(
        label         = label,
        url           = url,
        watch_type    = watch_type,
        summary_diff  = summary_diff,
        ai_summary    = ai_summary,
        price_hits    = price_hits,
        link_diff     = link_diff,
        meta_changes  = meta_changes,
        price_changes = price_changes,
    )

    chunks  = _split_message(message)
    success = True

    for i, chunk in enumerate(chunks):
        try:
            await bot.send_message(
                chat_id                  = user_id,
                text                     = chunk,
                parse_mode               = ParseMode.HTML,
                disable_web_page_preview = True,
            )
        except TelegramError as e:
            logger.error(
                f"[notifier] Failed to send alert chunk {i+1} "
                f"to {user_id}: {e}"
            )
            success = False

    if success:
        logger.info(
            f"[notifier] Alert sent to user={user_id} "
            f"label={label} chunks={len(chunks)}"
        )

    return success


async def send_digest(bot: Bot, user_id: int, changes: list) -> bool:
    if not changes:
        try:
            await bot.send_message(
                chat_id    = user_id,
                text       = "✅ <b>No changes detected</b> across your watched competitors since last check.",
                parse_mode = ParseMode.HTML,
            )
            return True
        except TelegramError:
            return False

    lines = ["📊 <b>Competitor Intelligence Digest</b>\n"]

    for change in changes:
        icon  = _watch_type_icon(change["watch_type"] if "watch_type" in change.keys() else "page")
        label = change["label"]
        time  = change["detected_at"][:16]
        lines.append(f"{icon} <b>{label}</b> — <i>{time}</i>")
        if change["ai_summary"]:
            lines.append(f"  🧠 {change['ai_summary'][:180]}")
        else:
            lines.append(f"  📝 Change detected — use /digest for details.")
        lines.append("")

    lines.append("Use /list to review all your watches.")

    chunks  = _split_message("\n".join(lines))
    success = True
    for chunk in chunks:
        try:
            await bot.send_message(
                chat_id                  = user_id,
                text                     = chunk,
                parse_mode               = ParseMode.HTML,
                disable_web_page_preview = True,
            )
        except TelegramError as e:
            logger.error(f"[notifier] Digest chunk failed for {user_id}: {e}")
            success = False

    return success


async def send_system_message(bot: Bot, user_id: int, text: str) -> bool:
    try:
        await bot.send_message(
            chat_id                  = user_id,
            text                     = text,
            parse_mode               = ParseMode.HTML,
            disable_web_page_preview = True,
        )
        return True
    except TelegramError as e:
        logger.error(f"[notifier] System message failed for {user_id}: {e}")
        return False


async def send_fetch_failure_alert(bot: Bot, user_id: int, label: str, url: str):
    """
    Alert user when a watch URL has been unreachable for multiple cycles.
    Keeps users informed so they can update the URL if it changed.
    """
    try:
        await bot.send_message(
            chat_id    = user_id,
            text       = (
                f"⚠️ <b>Watch Unreachable — {label}</b>\n\n"
                f"I haven't been able to reach:\n"
                f"<code>{url}</code>\n\n"
                f"This could be temporary. I'll keep trying.\n"
                f"If the URL changed, use /remove and /watch to update it."
            ),
            parse_mode = ParseMode.HTML,
            disable_web_page_preview = True,
        )
    except TelegramError as e:
        logger.error(f"[notifier] Fetch failure alert failed for {user_id}: {e}")