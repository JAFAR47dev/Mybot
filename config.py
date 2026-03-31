# config.py
import os
from dotenv import load_dotenv

load_dotenv()

# --- Bot ---
BOT_TOKEN    = os.getenv("BOT_TOKEN")
BOT_USERNAME = os.getenv("BOT_USERNAME", "RivalWatchBot")
ADMIN_IDS    = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]

# --- OpenRouter ---
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_MODEL   = os.getenv("OPENROUTER_MODEL", "mistralai/mixtral-8x7b-instruct")
OPENROUTER_URL     = "https://openrouter.ai/api/v1/chat/completions"

# --- Tiers ---
TIER_LIMITS = {
    "free": {
        "max_watches":        2,
        "check_interval_hrs": 24,
        "ai_summary":         False,
        "instant_alerts":     False,
    },
    "starter": {
        "max_watches":        5,
        "check_interval_hrs": 12,
        "ai_summary":         False,
        "instant_alerts":     False,
    },
    "pro": {
        "max_watches":        20,
        "check_interval_hrs": 1,
        "ai_summary":         True,
        "instant_alerts":     True,
    },
    "agency": {
        "max_watches":        99,
        "check_interval_hrs": 1,
        "ai_summary":         True,
        "instant_alerts":     True,
    },
}

# --- Pricing (USD reference only — for display) ---
TIER_PRICES_USD = {
    "starter": 9,
    "pro":     29,
    "agency":  79,
}

# --- Pricing (Telegram Stars — actual charge currency) ---
# 1 Star ≈ $0.013 USD
TIER_PRICES_STARS = {
    "starter": 250,    # ~$9
    "pro":     1000,   # ~$29
    "agency":  2500,   # ~$79
}

TIER_LABELS = {
    "free":    "🆓 Free",
    "starter": "⚡ Starter",
    "pro":     "🚀 Pro",
    "agency":  "🏢 Agency",
}

# --- Scraper ---
SCRAPER_TIMEOUT_SECS   = 15
SCRAPER_USER_AGENT     = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)
SCRAPER_MIN_DELAY_SECS = 2
SCRAPER_MAX_DELAY_SECS = 6

# --- Watch types ---
WATCH_TYPE_PAGE    = "page"
WATCH_TYPE_JOBS    = "jobs"
WATCH_TYPE_REVIEWS = "reviews"

WATCH_TYPE_LABELS = {
    "page":      "📄 Pricing / Landing Page",
    "jobs":      "💼 Job Postings",
    "reviews":   "⭐ Reviews (G2 / Trustpilot)",
    "pricing":   "💰 Pricing Page (deep scan)",
    "changelog": "📝 Changelog / Blog",
}

# --- AI prompt ---
AI_ANALYST_PROMPT = """You are a competitive intelligence analyst.
A user is monitoring a competitor's page and a change was detected.
Analyze the diff below and explain in 2-3 sentences:
1. What specifically changed
2. What it likely signals strategically
3. Any action the user should consider

Be direct and specific. No fluff. No bullet points. Plain paragraph only.

--- BEFORE ---
{before}

--- AFTER ---
{after}
"""

# --- Misc ---
DB_PATH          = os.getenv("DB_PATH", "competitorbot.db")
MAX_SNAPSHOT_LEN = 2000