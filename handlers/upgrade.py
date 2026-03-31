# handlers/upgrade.py
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice
from telegram.ext import ContextTypes
from telegram.constants import ParseMode
from telegram.error import TelegramError

from database.models import get_user, log_payment, set_user_tier
from services.notifier import send_system_message
from config import (
    TIER_LIMITS,
    TIER_PRICES_USD,
    TIER_PRICES_STARS,
    TIER_LABELS,
)

logger = logging.getLogger(__name__)


# ─── Keyboards ────────────────────────────────────────────────────────────────

def _build_plans_keyboard(current_tier: str) -> InlineKeyboardMarkup:
    buttons = []

    for tier, stars in TIER_PRICES_STARS.items():
        usd_approx = TIER_PRICES_USD[tier]
        if tier == current_tier:
            buttons.append([
                InlineKeyboardButton(
                    f"✅ {TIER_LABELS[tier]} — Current Plan",
                    callback_data = "upgrade_noop",
                )
            ])
        else:
            buttons.append([
                InlineKeyboardButton(
                    f"{TIER_LABELS[tier]} — {stars} ⭐ ",
                    callback_data = f"upgrade_select_{tier}",
                )
            ])

    buttons.append([InlineKeyboardButton("❌ Cancel", callback_data="upgrade_cancel")])
    return InlineKeyboardMarkup(buttons)


def _build_confirm_keyboard(tier: str) -> InlineKeyboardMarkup:
    stars = TIER_PRICES_STARS[tier]
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                f"⭐ Pay {stars} Stars",
                callback_data = f"upgrade_pay_{tier}",
            ),
        ],
        [InlineKeyboardButton("↩️ Back", callback_data="upgrade_back")],
    ])


# ─── Messages ─────────────────────────────────────────────────────────────────

def _plans_message(current_tier: str) -> str:
    lines = ["🚀 <b>Upgrade Your Plan</b>\n"]

    for tier, limits in TIER_LIMITS.items():
        if tier == "free":
            continue

        stars     = TIER_PRICES_STARS[tier]
        usd       = TIER_PRICES_USD[tier]
        label     = TIER_LABELS[tier]
        ai_str    = "✅ AI summaries"   if limits["ai_summary"]     else "❌ AI summaries"
        alert_str = "✅ Instant alerts" if limits["instant_alerts"] else "❌ Instant alerts"
        current   = " <i>(current)</i>" if tier == current_tier     else ""

        lines.append(
            f"{label}{current} — <b>{stars} ⭐ </b>\n"
            f"  • {limits['max_watches']} competitors\n"
            f"  • Checks every {limits['check_interval_hrs']}h\n"
            f"  • {ai_str}\n"
            f"  • {alert_str}\n"
        )

    lines.append(
        "\n💡 <i>Payments use Telegram Stars — "
        "instant, secure, never leaves Telegram.</i>"
    )
    return "\n".join(lines)


# ─── Stars Invoice ────────────────────────────────────────────────────────────

async def _send_stars_invoice(
    context: ContextTypes.DEFAULT_TYPE,
    user_id: int,
    tier: str,
) -> bool:
    """Send a Telegram Stars invoice to the user."""
    stars     = TIER_PRICES_STARS[tier]
    label     = TIER_LABELS[tier]
    limits    = TIER_LIMITS[tier]

    try:
        await context.bot.send_invoice(
            chat_id        = user_id,
            title          = f"RivalWatch {label} Plan",
            description    = (
                f"{limits['max_watches']} competitors • "
                f"every {limits['check_interval_hrs']}h checks • "
                f"{'AI summaries • ' if limits['ai_summary'] else ''}"
                f"30 days access"
            ),
            payload        = f"{user_id}_{tier}",
            currency       = "XTR",        # XTR = Telegram Stars
            prices         = [LabeledPrice(
                label  = f"{label} — 30 days",
                amount = stars,
            )],
            provider_token = "",           # empty string required for Stars
        )
        logger.info(f"Stars invoice sent to user={user_id} tier={tier} stars={stars}")
        return True

    except TelegramError as e:
        logger.error(f"Stars invoice failed for user={user_id}: {e}")
        return False


# ─── Handlers ─────────────────────────────────────────────────────────────────

async def upgrade_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point — /upgrade command."""
    user_tg = update.effective_user
    user    = get_user(user_tg.id)

    if not user:
        await _reply(update, "Please send /start first.")
        return

    await _reply(update, _plans_message(user["tier"]), _build_plans_keyboard(user["tier"]))


async def upgrade_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data  = query.data

    # --- No-op / cancel ---
    if data in ("upgrade_cancel", "upgrade_noop"):
        await query.message.edit_text("↩️ No changes made.", parse_mode=ParseMode.HTML)
        return

    # --- Back to plans ---
    if data == "upgrade_back":
        user = get_user(update.effective_user.id)
        await query.message.edit_text(
            text         = _plans_message(user["tier"]),
            parse_mode   = ParseMode.HTML,
            reply_markup = _build_plans_keyboard(user["tier"]),
        )
        return

    # --- Plan selected — show confirm screen ---
    if data.startswith("upgrade_select_"):
        tier   = data.replace("upgrade_select_", "")
        stars  = TIER_PRICES_STARS.get(tier)
        label  = TIER_LABELS.get(tier)
        limits = TIER_LIMITS.get(tier)
        usd    = TIER_PRICES_USD.get(tier)

        if not stars:
            await query.message.edit_text("⚠️ Invalid plan selected.")
            return

        await query.message.edit_text(
            text = (
                f"🚀 <b>{label} — {stars} ⭐ </b>\n\n"
                f"✅ {limits['max_watches']} competitors\n"
                f"✅ Checks every {limits['check_interval_hrs']}h\n"
                f"{'✅' if limits['ai_summary']     else '❌'} AI summaries\n"
                f"{'✅' if limits['instant_alerts'] else '❌'} Instant alerts\n"
                f"✅ 30 days access\n\n"
                f"Payment is made with Telegram Stars — "
                f"instant and secure, never leaves Telegram.\n\n"
                f"Tap <b>Pay {stars} ⭐</b> to proceed."
            ),
            parse_mode   = ParseMode.HTML,
            reply_markup = _build_confirm_keyboard(tier),
        )
        return

    # --- Payment confirmed — send Stars invoice ---
    if data.startswith("upgrade_pay_"):
        tier    = data.replace("upgrade_pay_", "")
        stars   = TIER_PRICES_STARS.get(tier)
        user_id = update.effective_user.id

        if not stars:
            await query.message.edit_text("⚠️ Invalid plan.")
            return

        await query.message.edit_text(
            "⭐ Sending your payment invoice...",
            parse_mode = ParseMode.HTML,
        )

        success = await _send_stars_invoice(context, user_id, tier)

        if not success:
            await query.message.edit_text(
                "⚠️ Could not generate invoice. Please try again later."
            )


# ─── Pre-checkout Handler ─────────────────────────────────────────────────────

async def pre_checkout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Telegram calls this before charging Stars.
    Must be answered within 10 seconds — always approve here.
    """
    query = update.pre_checkout_query
    await query.answer(ok=True)
    logger.info(f"Pre-checkout approved for user={query.from_user.id}")


# ─── Successful Payment Handler ───────────────────────────────────────────────

async def successful_payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Fires after Telegram confirms Stars have been charged.
    Activates the user's tier immediately.
    """
    payment    = update.message.successful_payment
    payload    = payment.invoice_payload     # "{user_id}_{tier}"
    stars_paid = payment.total_amount
    user_id    = update.effective_user.id

    # Parse tier from payload
    try:
        tier = payload.split("_", 1)[1]
    except Exception:
        logger.error(f"Could not parse Stars payload: {payload}")
        return

    # Activate tier in DB
    set_user_tier(user_id, tier)

    # Log payment record
    log_payment(
        user_id    = user_id,
        payment_id = f"stars_{user_id}_{tier}_{stars_paid}",
        amount_usd = round(stars_paid * 0.013, 2),
        tier       = tier,
    )

    label      = TIER_LABELS.get(tier, tier)
    limits     = TIER_LIMITS[tier]
    ai_enabled = limits["ai_summary"]
    interval   = limits["check_interval_hrs"]

    await send_system_message(
        bot     = context.bot,
        user_id = user_id,
        text    = (
            f"🎉 <b>Payment confirmed — {stars_paid} ⭐ received!</b>\n\n"
            f"You're now on the <b>{label}</b> plan.\n\n"
            f"✅ Monitors running every <b>{interval}h</b>\n"
            f"{'✅' if ai_enabled else '❌'} AI summaries\n"
            f"✅ 30 days access starts now\n\n"
            f"Use /list to review your active watches.\n"
            f"Use /watch to add more competitors."
        ),
    )
    logger.info(
        f"Stars payment confirmed — user={user_id} "
        f"tier={tier} stars={stars_paid}"
    )


# ─── Shared reply helper ──────────────────────────────────────────────────────

async def _reply(
    update: Update,
    text: str,
    keyboard: InlineKeyboardMarkup = None,
):
    kwargs = dict(
        text                     = text,
        parse_mode               = ParseMode.HTML,
        reply_markup             = keyboard,
        disable_web_page_preview = True,
    )
    if update.message:
        await update.message.reply_text(**kwargs)
    elif update.callback_query:
        await update.callback_query.message.reply_text(**kwargs)