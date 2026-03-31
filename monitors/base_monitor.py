# monitors/base_monitor.py
import logging
from database.models import (
    update_watch_hash,
    update_watch_changed,
    log_change,
    mark_notified,
    get_last_snapshot,
)
from services.scraper import fetch_and_extract
from services.differ import hash_content, has_changed, build_change_payload
from services.ai_analyst import analyse_change
from services.notifier import send_alert
from config import TIER_LIMITS

logger = logging.getLogger(__name__)


async def process_watch(bot, watch) -> bool:
    """
    Shared pipeline for all watch types.
    Fetch → Diff → Log → AI → Alert.
    Returns True if a change was detected.
    """
    watch_id   = watch["id"]
    user_id    = watch["user_id"]
    label      = watch["label"]
    url        = watch["url"]
    watch_type = watch["watch_type"]
    last_hash  = watch["last_hash"]
    tier       = watch["tier"]

    tier_config = TIER_LIMITS.get(tier, TIER_LIMITS["free"])
    ai_enabled  = tier_config["ai_summary"]

    # --- Fetch current content ---
    content = await fetch_and_extract(url)
    if not content:
        logger.warning(f"[watch {watch_id}] Fetch failed for {url} — skipping")
        return False

    new_text  = content["text"]
    new_links = content["links"]

    # --- First run: store baseline hash and exit ---
    if last_hash is None:
        update_watch_hash(watch_id, hash_content(new_text))
        logger.info(f"[watch {watch_id}] First run — baseline stored for {label}")
        return False

    # --- No change detected ---
    if not has_changed(new_text, last_hash):
        update_watch_hash(watch_id, last_hash)
        logger.info(f"[watch {watch_id}] No change for {label}")
        return False

    # --- Fetch previous snapshot from change_log for real diff ---
    old_snapshot = get_last_snapshot(watch_id)
    old_text     = old_snapshot["new_snapshot"] if old_snapshot else ""

    # --- Build change payload ---
    payload = build_change_payload(
        old_text  = old_text,
        new_text  = new_text,
        old_links = [],
        new_links = new_links,
    )

    # --- AI analysis (Pro/Agency only) ---
    ai_summary = None
    if ai_enabled:
        ai_summary = await analyse_change(
            label        = label,
            url          = url,
            watch_type   = watch_type,
            summary_diff = payload["summary_diff"],
            price_hits   = payload["price_hits"],
            link_diff    = payload["link_diff"],
        )

    # --- Log to DB ---
    change_id = log_change(
        watch_id     = watch_id,
        user_id      = user_id,
        old_snapshot = payload["old_snapshot"],
        new_snapshot = payload["new_snapshot"],
        ai_summary   = ai_summary,
    )

    # --- Update watch record ---
    update_watch_changed(watch_id, payload["new_hash"])

    # --- Send alert ---
    sent = await send_alert(
        bot          = bot,
        user_id      = user_id,
        label        = label,
        url          = url,
        watch_type   = watch_type,
        summary_diff = payload["summary_diff"],
        ai_summary   = ai_summary,
        price_hits   = payload["price_hits"],
        link_diff    = payload["link_diff"],
    )

    if sent:
        mark_notified(change_id)

    return True
