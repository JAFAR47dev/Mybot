# utils/helpers.py
import re
import logging
from urllib.parse import urlparse, urlunparse
from config import TIER_LIMITS

logger = logging.getLogger(__name__)


# ─── URL Utilities ────────────────────────────────────────────────────────────

def normalize_url(url: str) -> str:
    """
    Normalize a URL for consistent storage and comparison.
    - Lowercases scheme and host
    - Strips trailing slashes
    - Removes default ports
    - Strips common tracking params
    """
    url = url.strip()
    if not url.startswith("http"):
        url = "https://" + url

    try:
        parsed = urlparse(url)
        clean  = parsed._replace(
            scheme   = parsed.scheme.lower(),
            netloc   = parsed.netloc.lower(),
            fragment = "",    # strip anchors
        )
        return urlunparse(clean).rstrip("/")
    except Exception:
        return url.rstrip("/")


def is_valid_url(url: str) -> bool:
    """Basic URL validation — scheme and netloc must be present."""
    try:
        parsed = urlparse(url)
        return all([parsed.scheme in ("http", "https"), parsed.netloc])
    except Exception:
        return False


def extract_domain(url: str) -> str:
    """Return just the domain from a URL — e.g. 'notion.so'"""
    try:
        return urlparse(url).netloc.lower().lstrip("www.")
    except Exception:
        return url


def is_same_domain(url1: str, url2: str) -> bool:
    """Check if two URLs share the same domain."""
    return extract_domain(url1) == extract_domain(url2)


# ─── Tier Utilities ───────────────────────────────────────────────────────────

def get_tier_limit(tier: str, key: str):
    """Safe accessor for tier limit values."""
    return TIER_LIMITS.get(tier, TIER_LIMITS["free"]).get(key)


def can_add_watch(tier: str, current_count: int) -> bool:
    """Return True if user has not hit their watch limit."""
    max_watches = get_tier_limit(tier, "max_watches")
    return current_count < max_watches


def watches_remaining(tier: str, current_count: int) -> int:
    """Return how many more watches the user can add."""
    max_watches = get_tier_limit(tier, "max_watches")
    return max(0, max_watches - current_count)


def tier_can_use_ai(tier: str) -> bool:
    return bool(get_tier_limit(tier, "ai_summary"))


def tier_has_instant_alerts(tier: str) -> bool:
    return bool(get_tier_limit(tier, "instant_alerts"))


# ─── Text Utilities ───────────────────────────────────────────────────────────

def truncate(text: str, max_len: int = 100, suffix: str = "...") -> str:
    """Truncate text to max_len characters."""
    if not text:
        return ""
    return text if len(text) <= max_len else text[:max_len - len(suffix)] + suffix


def clean_text(text: str) -> str:
    """
    Strip excessive whitespace and non-printable characters.
    Useful before hashing or storing snapshots.
    """
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def escape_html(text: str) -> str:
    """Escape HTML special characters for safe Telegram HTML messages."""
    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def slugify(text: str) -> str:
    """Convert a label to a URL-safe slug — used for order IDs."""
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    return text.strip("-")


# ─── Formatting Utilities ─────────────────────────────────────────────────────

def format_tier_badge(tier: str) -> str:
    badges = {
        "free":    "🆓",
        "starter": "⚡",
        "pro":     "🚀",
        "agency":  "🏢",
    }
    return badges.get(tier, "❓")


def format_check_interval(hrs: int) -> str:
    """Human-readable interval string."""
    if hrs < 1:
        return f"{int(hrs * 60)} minutes"
    if hrs == 1:
        return "1 hour"
    if hrs < 24:
        return f"{hrs} hours"
    return f"{hrs // 24} day(s)"


def format_watch_summary(watch) -> str:
    """
    One-line summary of a watch — used in broadcast messages
    and admin views where space is tight.
    """
    domain     = extract_domain(watch["url"])
    watch_type = watch["watch_type"]
    icons      = {"page": "📄", "jobs": "💼", "reviews": "⭐"}
    icon       = icons.get(watch_type, "🔍")
    return f"{icon} {watch['label']} ({domain})"


# ─── Validation Utilities ─────────────────────────────────────────────────────

def is_valid_tier(tier: str) -> bool:
    return tier in TIER_LIMITS


def sanitize_label(label: str) -> str:
    """
    Strip HTML tags and limit length for competitor labels.
    Prevents injection into Telegram HTML messages.
    """
    label = re.sub(r"<[^>]+>", "", label)
    return label.strip()[:50]
    
def is_quiet_hours(user: dict) -> bool:
    """
    Returns True if current UTC time falls within the user's quiet hours.
    Handles overnight ranges e.g. 22:00 → 07:00 correctly.
    """
    from datetime import datetime, timezone

    if not user["quiet_hours_on"]:
        return False

    start = user["quiet_hours_start"]
    end   = user["quiet_hours_end"]

    if start is None or end is None:
        return False

    current_hr = datetime.now(timezone.utc).hour

    if start < end:
        # Same-day range e.g. 09:00 → 17:00
        return start <= current_hr < end
    else:
        # Overnight range e.g. 22:00 → 07:00
        return current_hr >= start or current_hr < end

