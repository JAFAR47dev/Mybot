# payments/stars.py
import logging
from telegram import Bot, LabeledPrice, Update
from telegram.ext import ContextTypes
from telegram.error import TelegramError

logger = logging.getLogger(__name__)


async def send_stars_invoice(
    bot: Bot,
    user_id: int,
    tier: str,
    star_amount: int,
) -> bool:
    """
    Send a Telegram Stars invoice directly to the user's chat.
    Returns True on success.
    """
    from config import TIER_LABELS

    tier_label = TIER_LABELS.get(tier, tier)

    try:
        await bot.send_invoice(
            chat_id         = user_id,
            title           = f"RivalWatch {tier_label} Plan",
            description     = (
                f"30 days of {tier_label} competitor monitoring. "
                f"Instant alerts, AI analysis, and automatic renewal reminders."
            ),
            payload         = f"{user_id}_{tier}",   # passed back in pre_checkout
            currency        = "XTR",                  # XTR = Telegram Stars
            prices          = [
                LabeledPrice(
                    label  = f"{tier_label} — 30 days",
                    amount = star_amount,
                )
            ],
            provider_token  = "",                     # empty string for Stars
        )
        logger.info(f"Stars invoice sent to user {user_id} for tier={tier}")
        return True

    except TelegramError as e:
        logger.error(f"Failed to send Stars invoice to {user_id}: {e}")
        return False


async def handle_pre_checkout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Telegram calls this before charging the user.
    You MUST answer within 10 seconds or the payment is cancelled.
    Always approve here — validation happens in successful_payment_handler.
    """
    query = update.pre_checkout_query
    await query.answer(ok=True)
    logger.info(f"Pre-checkout approved for user {query.from_user.id}")


async def handle_successful_payment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Fires after Telegram confirms Stars have been charged.
    This is where we activate the user's tier.
    """
    from database.models import set_user_tier, log_payment
    from services.notifier import send_system_message
    from config import TIER_LABELS, TIER_LIMITS

    payment    = update.message.successful_payment
    payload    = payment.invoice_payload          # "{user_id}_{tier}"
    stars_paid = payment.total_amount
    user_id    = update.effective_user.id

    # Parse payload
    try:
        parts   = payload.split("_", 1)
        tier    = parts[1]
    except Exception:
        logger.error(f"Could not parse Stars payment payload: {payload}")
        return

    # Activate tier
    set_user_tier(user_id, tier)

    # Log payment
    log_payment(
        user_id    = user_id,
        payment_id = f"stars_{user_id}_{tier}_{stars_paid}",
        amount_usd = stars_paid * 0.013,
        tier       = tier,
    )

    tier_label = TIER_LABELS.get(tier, tier)
    interval   = TIER_LIMITS[tier]["check_interval_hrs"]
    ai_enabled = TIER_LIMITS[tier]["ai_summary"]

    await send_system_message(
        bot     = context.bot,
        user_id = user_id,
        text    = (
            f"🎉 <b>Payment confirmed — {stars_paid} Stars received!</b>\n\n"
            f"You're now on the <b>{tier_label}</b> plan.\n\n"
            f"✅ Monitors running every <b>{interval}h</b>\n"
            f"{'✅' if ai_enabled else '❌'} AI summaries\n"
            f"✅ 30 days access\n\n"
            f"Use /list to review your active watches."
        ),
    )
    logger.info(f"Stars payment confirmed — user={user_id} tier={tier} stars={stars_paid}")