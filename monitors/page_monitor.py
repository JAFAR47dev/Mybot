# monitors/page_monitor.py
import logging
from database.models import get_watches_due
from monitors.base_monitor import process_watch
from config import TIER_LIMITS

logger = logging.getLogger(__name__)


async def run_page_monitor(bot):
    """
    Check all active page watches that are due based on tier interval.
    Called by the scheduler every hour.
    """
    changes_detected = 0

    for tier, config in TIER_LIMITS.items():
        interval_hrs = config["check_interval_hrs"]
        due_watches  = get_watches_due(interval_hrs)

        page_watches = [
            w for w in due_watches
            if w["watch_type"] == "page" and w["tier"] == tier
        ]

        if not page_watches:
            continue

        logger.info(f"[page_monitor] Checking {len(page_watches)} page watches for tier={tier}")

        for watch in page_watches:
            try:
                changed = await process_watch(bot, watch)
                if changed:
                    changes_detected += 1
            except Exception as e:
                logger.error(f"[page_monitor] Error processing watch {watch['id']}: {e}")

    logger.info(f"[page_monitor] Run complete — {changes_detected} change(s) detected")
    return changes_detected