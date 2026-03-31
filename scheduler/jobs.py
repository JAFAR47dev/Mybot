# scheduler/jobs.py
import logging
from datetime import time as dtime
from telegram.ext import Application
from database.models import get_all_users, get_recent_changes, get_unnotified_changes, mark_notified
from services.notifier import send_digest, send_alert
from monitors.page_monitor import run_page_monitor
from monitors.jobs_monitor import run_jobs_monitor
from monitors.reviews_monitor import run_reviews_monitor

logger = logging.getLogger(__name__)


async def run_all_monitors(context):
    bot = context.bot
    logger.info("[scheduler] Starting monitor run...")
    try:
        page_changes    = await run_page_monitor(bot)
        jobs_changes    = await run_jobs_monitor(bot)
        reviews_changes = await run_reviews_monitor(bot)
        total = page_changes + jobs_changes + reviews_changes
        logger.info(
            f"[scheduler] Run complete — "
            f"page={page_changes}, jobs={jobs_changes}, "
            f"reviews={reviews_changes}, total={total}"
        )
    except Exception as e:
        logger.error(f"[scheduler] Monitor run failed: {e}")


async def send_daily_digests(context):
    bot          = context.bot
    users        = get_all_users()
    digest_tiers = {"free", "starter"}

    for user in users:
        if user["tier"] not in digest_tiers:
            continue
        try:
            changes = get_recent_changes(user["user_id"], limit=10)
            await send_digest(bot, user["user_id"], changes)
            logger.info(f"[scheduler] Digest sent to user {user['user_id']}")
        except Exception as e:
            logger.error(f"[scheduler] Digest failed for user {user['user_id']}: {e}")


async def run_retry_unnotified(context):
    bot     = context.bot
    pending = get_unnotified_changes()

    if not pending:
        return

    logger.info(f"[scheduler] Retrying {len(pending)} unnotified change(s)")

    for change in pending:
        try:
            sent = await send_alert(
                bot          = bot,
                user_id      = change["user_id"],
                label        = change["label"],
                url          = change["url"],
                watch_type   = change["watch_type"],
                summary_diff = change["new_snapshot"] or "Change detected.",
                ai_summary   = change["ai_summary"],
            )
            if sent:
                mark_notified(change["id"])
        except Exception as e:
            logger.error(f"[scheduler] Retry failed for change {change['id']}: {e}")

async def send_renewal_reminders(context):
    """
    Send escalating renewal reminders at 5 days, 2 days, and 1 day before expiry.
    Runs daily at 10:00 UTC.
    """
    from database.models import get_expiring_subscriptions
    from services.notifier import send_system_message
    from config import TIER_LABELS, TIER_PRICES_USD

    bot = context.bot

    for days_left, urgency in [(5, "low"), (2, "medium"), (1, "high")]:
        expiring = get_expiring_subscriptions(days_ahead=days_left)

        for user in expiring:
            tier       = user["tier"]
            label      = TIER_LABELS.get(tier, tier)
            price      = TIER_PRICES_USD.get(tier, 0)
            expires_at = user["tier_expires_at"][:10]

            if urgency == "low":
                text = (
                    f"⏰ <b>Your {label} plan renews in 5 days</b>\n\n"
                    f"Your access expires on <b>{expires_at}</b>.\n\n"
                    f"Renew now to keep your competitors under watch "
                    f"without interruption.\n\n"
                    f"💳 <b>${price}/mo</b> — use /upgrade to renew."
                )

            elif urgency == "medium":
                text = (
                    f"⚠️ <b>2 days left on your {label} plan</b>\n\n"
                    f"Your monitors will pause and AI summaries will stop "
                    f"on <b>{expires_at}</b> unless you renew.\n\n"
                    f"Your competitors don't take days off — neither should "
                    f"your intelligence.\n\n"
                    f"👉 /upgrade to renew now — ${price}/mo."
                )

            else:  # high urgency
                text = (
                    f"🚨 <b>Last day — your {label} plan expires tomorrow</b>\n\n"
                    f"After <b>{expires_at}</b> you'll lose:\n"
                    f"❌ Instant alerts\n"
                    f"❌ AI-powered change analysis\n"
                    f"❌ Hourly monitoring\n\n"
                    f"Everything reverts to free tier (24h checks, no AI).\n\n"
                    f"<b>Renew in 60 seconds:</b> /upgrade"
                )

            try:
                await send_system_message(bot, user["user_id"], text)
                logger.info(
                    f"[scheduler] Renewal reminder ({urgency}) sent "
                    f"to user {user['user_id']}"
                )
            except Exception as e:
                logger.error(
                    f"[scheduler] Reminder failed for user "
                    f"{user['user_id']}: {e}"
                )


async def downgrade_expired_subscriptions(context):
    """
    Downgrade users whose paid tier has expired.
    Sends a win-back message after downgrading.
    Runs daily at 00:00 UTC.
    """
    from database.models import get_expired_subscriptions, downgrade_user
    from services.notifier import send_system_message
    from config import TIER_LABELS

    bot = context.bot

    expired = get_expired_subscriptions()

    for user in expired:
        user_id    = user["user_id"]
        old_tier   = user["tier"]
        old_label  = TIER_LABELS.get(old_tier, old_tier)

        downgrade_user(user_id)
        logger.info(f"[scheduler] Downgraded user {user_id} from {old_tier} to free")

        text = (
            f"😔 <b>Your {old_label} plan has expired</b>\n\n"
            f"You've been moved back to the free tier:\n"
            f"❌ AI summaries paused\n"
            f"❌ Instant alerts paused\n"
            f"❌ Checks reduced to every 24h\n\n"
            f"Your watches are still saved — nothing was deleted.\n\n"
            f"<b>Renew anytime to pick up exactly where you left off:</b>\n"
            f"👉 /upgrade"
        )

        try:
            await send_system_message(bot, user_id, text)
        except Exception as e:
            logger.error(f"[scheduler] Downgrade message failed for {user_id}: {e}")


# ─── Updated setup_scheduler ─────────────────────────────────────────────────

def setup_scheduler(app: Application):
    jq = app.job_queue

    # Monitors — every hour
    jq.run_repeating(
        callback = run_all_monitors,
        interval = 3600,
        first    = 60,
        name     = "monitor_run",
    )

    # Daily digest for free/starter at 09:00 UTC
    jq.run_daily(
        callback = send_daily_digests,
        time     = dtime(hour=9, minute=0),
        name     = "daily_digest",
    )

    # Retry unnotified alerts every 6 hours
    jq.run_repeating(
        callback = run_retry_unnotified,
        interval = 21600,
        first    = 300,
        name     = "retry_unnotified",
    )

    # Renewal reminders daily at 10:00 UTC
    jq.run_daily(
        callback = send_renewal_reminders,
        time     = dtime(hour=10, minute=0),
        name     = "renewal_reminders",
    )

    # Downgrade expired subscriptions daily at 00:00 UTC
    jq.run_daily(
        callback = downgrade_expired_subscriptions,
        time     = dtime(hour=0, minute=0),
        name     = "downgrade_expired",
    )

    logger.info(
        "[scheduler] Jobs registered: monitor_run (1hr), "
        "daily_digest (09:00), retry_unnotified (6hr), "
        "renewal_reminders (10:00), downgrade_expired (00:00)"
    )
   