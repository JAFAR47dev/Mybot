# payments/nowpayments.py
import httpx
import hmac
import hashlib
import json
import logging
from config import NOWPAYMENTS_API_KEY, NOWPAYMENTS_IPN_SECRET

logger = logging.getLogger(__name__)

NOWPAYMENTS_BASE    = "https://api.nowpayments.io/v1"
INVOICE_ENDPOINT    = f"{NOWPAYMENTS_BASE}/invoice"
PAYMENT_ENDPOINT    = f"{NOWPAYMENTS_BASE}/payment"
STATUS_ENDPOINT     = f"{NOWPAYMENTS_BASE}/payment/{{payment_id}}"
MIN_AMOUNT_ENDPOINT = f"{NOWPAYMENTS_BASE}/min-amount"
CURRENCIES_ENDPOINT = f"{NOWPAYMENTS_BASE}/currencies"


# ─── Invoice Creation ─────────────────────────────────────────────────────────

async def create_invoice(
    user_id: int,
    tier: str,
    amount_usd: float,
    success_url: str = "https://t.me/PeriscopeIntelBot",
    cancel_url:  str = "https://t.me/PeriscopeIntelBot",
) -> dict | None:
    """
    Create a NOWPayments hosted invoice.
    Returns full response dict or None on failure.
    """
    payload = {
        "price_amount":      amount_usd,
        "price_currency":    "usd",
        "pay_currency":      "usdttrc20",
        "order_id":          f"{user_id}_{tier}",
        "order_description": f"CompetitorBot {tier} plan — monthly",
        "success_url":       success_url,
        "cancel_url":        cancel_url,
        "is_fixed_rate":     True,
        "is_fee_paid_by_user": False,
    }

    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                INVOICE_ENDPOINT,
                headers = {
                    "x-api-key":    NOWPAYMENTS_API_KEY,
                    "Content-Type": "application/json",
                },
                json = payload,
            )
            r.raise_for_status()
            data = r.json()
            logger.info(f"Invoice created for user {user_id} tier={tier}: {data.get('id')}")
            return data
    except httpx.HTTPStatusError as e:
        logger.error(f"NOWPayments invoice error {e.response.status_code}: {e.response.text}")
    except Exception as e:
        logger.error(f"NOWPayments invoice unexpected error: {e}")
    return None


# ─── Payment Status ───────────────────────────────────────────────────────────

async def get_payment_status(payment_id: str) -> dict | None:
    """
    Poll a specific payment's current status.
    Useful for manual status checks.
    """
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                STATUS_ENDPOINT.format(payment_id=payment_id),
                headers={"x-api-key": NOWPAYMENTS_API_KEY},
            )
            r.raise_for_status()
            return r.json()
    except Exception as e:
        logger.error(f"NOWPayments status check failed: {e}")
    return None


async def get_available_currencies() -> list[str]:
    """Return list of currencies NOWPayments currently accepts."""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.get(
                CURRENCIES_ENDPOINT,
                headers={"x-api-key": NOWPAYMENTS_API_KEY},
            )
            r.raise_for_status()
            data = r.json()
            return data.get("currencies", [])
    except Exception as e:
        logger.error(f"NOWPayments currencies fetch failed: {e}")
    return []


# ─── IPN Verification ─────────────────────────────────────────────────────────

def verify_ipn_signature(payload_bytes: bytes, received_sig: str) -> bool:
    """
    Verify NOWPayments IPN webhook signature.
    Call this before processing any IPN payload.
    """
    if not NOWPAYMENTS_IPN_SECRET:
        logger.warning("IPN secret not set — skipping signature verification")
        return True

    try:
        payload_dict = json.loads(payload_bytes)
        # NOWPayments signs a sorted JSON payload
        sorted_payload = json.dumps(payload_dict, sort_keys=True, separators=(",", ":"))
        expected_sig   = hmac.new(
            NOWPAYMENTS_IPN_SECRET.encode(),
            sorted_payload.encode(),
            hashlib.sha512,
        ).hexdigest()
        return hmac.compare_digest(expected_sig, received_sig)
    except Exception as e:
        logger.error(f"IPN signature verification error: {e}")
        return False


def parse_ipn_payload(payload: dict) -> dict:
    """
    Normalize an IPN payload into the fields we care about.
    NOWPayments IPN docs: https://documenter.getpostman.com/view/7907941/2s93JqTRWN
    """
    return {
        "payment_id":     str(payload.get("payment_id", "")),
        "order_id":       str(payload.get("order_id", "")),
        "payment_status": str(payload.get("payment_status", "")),
        "price_amount":   float(payload.get("price_amount", 0)),
        "price_currency": str(payload.get("price_currency", "")),
        "pay_amount":     float(payload.get("pay_amount", 0)),
        "pay_currency":   str(payload.get("pay_currency", "")),
        "actually_paid":  float(payload.get("actually_paid", 0)),
        "outcome_amount": float(payload.get("outcome_amount", 0)),
    }


def is_payment_complete(parsed: dict) -> bool:
    """
    Return True only for terminal success statuses.
    NOWPayments statuses: waiting → confirming → confirmed → finished
    """
    return parsed["payment_status"] in {"finished", "confirmed"}


def extract_user_tier_from_order(order_id: str) -> tuple[int, str] | None:
    """
    Parse order_id back into (user_id, tier).
    order_id format: '{user_id}_{tier}'
    """
    try:
        parts = order_id.split("_", 1)
        if len(parts) != 2:
            return None
        return int(parts[0]), parts[1]
    except Exception:
        return None


# ─── Full IPN Handler ─────────────────────────────────────────────────────────

async def process_ipn(
    payload_bytes: bytes,
    signature: str,
    bot,
) -> bool:
    """
    Full IPN processing pipeline.
    Call this from your webhook route handler.
    Returns True if payment was successfully activated.
    """
    # 1. Verify signature
    if not verify_ipn_signature(payload_bytes, signature):
        logger.warning("IPN signature mismatch — ignoring payload")
        return False

    # 2. Parse payload
    try:
        raw     = json.loads(payload_bytes)
        parsed  = parse_ipn_payload(raw)
    except Exception as e:
        logger.error(f"IPN payload parse error: {e}")
        return False

    # 3. Check completion
    if not is_payment_complete(parsed):
        logger.info(f"IPN status={parsed['payment_status']} — not yet complete, ignoring")
        return False

    # 4. Extract user + tier from order_id
    result = extract_user_tier_from_order(parsed["order_id"])
    if not result:
        logger.error(f"Could not parse order_id: {parsed['order_id']}")
        return False

    user_id, tier = result

    # 5. Update DB
    from database.models import confirm_payment, set_user_tier, get_payment
    confirm_payment(parsed["payment_id"])
    set_user_tier(user_id, tier)

    # 6. Notify user
    from services.notifier import send_system_message
    from config import TIER_LABELS, TIER_LIMITS
    await send_system_message(
        bot     = bot,
        user_id = user_id,
        text    = (
            f"🎉 <b>Payment confirmed!</b>\n\n"
            f"You're now on the <b>{TIER_LABELS.get(tier, tier)}</b> plan.\n\n"
            f"Monitors now run every "
            f"<b>{TIER_LIMITS[tier]['check_interval_hrs']}h</b>.\n"
            f"Use /list to review your active watches."
        ),
    )

    logger.info(f"Payment processed — user={user_id} tier={tier}")
    return True
