# monitors/reviews_monitor.py
import httpx
import logging
from bs4 import BeautifulSoup
from database.models import (
    get_watches_due,
    update_watch_hash,
    update_watch_changed,
    log_change,
    mark_notified,
)
from services.differ import (
    hash_content,
    has_changed,
    truncate_snapshot,
)
from services.ai_analyst import analyse_change
from services.notifier import send_alert
from config import TIER_LIMITS, SCRAPER_TIMEOUT_SECS, SCRAPER_USER_AGENT

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent":      SCRAPER_USER_AGENT,
    "Accept-Language": "en-US,en;q=0.5",
}


# ─── Platform Detectors ───────────────────────────────────────────────────────

def _detect_platform(url: str) -> str | None:
    if "g2.com" in url:
        return "g2"
    if "trustpilot.com" in url:
        return "trustpilot"
    if "capterra.com" in url:
        return "capterra"
    if "producthunt.com" in url:
        return "producthunt"
    return None


# ─── Platform Scrapers ────────────────────────────────────────────────────────

async def _fetch_g2_reviews(url: str) -> list[dict]:
    """
    Scrape recent reviews from a G2 product page.
    Returns list of {rating, title, body, date} dicts.
    """
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=SCRAPER_TIMEOUT_SECS, follow_redirects=True) as client:
            r = await client.get(url)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")

        reviews = []
        cards = soup.select("div[itemprop='review']")

        for card in cards[:20]:
            title  = card.select_one("[itemprop='name']")
            body   = card.select_one("[itemprop='reviewBody']")
            rating = card.select_one("[itemprop='ratingValue']")
            date   = card.select_one("[itemprop='datePublished']")

            reviews.append({
                "title":  title.get_text(strip=True)  if title  else "",
                "body":   body.get_text(strip=True)   if body   else "",
                "rating": rating.get("content", "")   if rating else "",
                "date":   date.get("content", "")     if date   else "",
            })

        return reviews

    except Exception as e:
        logger.error(f"[reviews_monitor] G2 scrape failed for {url}: {e}")
        return []


async def _fetch_trustpilot_reviews(url: str) -> list[dict]:
    """
    Scrape recent reviews from a Trustpilot business page.
    Returns list of {rating, title, body, date} dicts.
    """
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=SCRAPER_TIMEOUT_SECS, follow_redirects=True) as client:
            r = await client.get(url)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")

        reviews = []
        cards = soup.select("article[data-service-review-card-paper]")

        for card in cards[:20]:
            title  = card.select_one("[data-service-review-title-typography]")
            body   = card.select_one("[data-service-review-text-typography]")
            rating = card.select_one("[data-service-review-rating]")
            date   = card.select_one("time")

            reviews.append({
                "title":  title.get_text(strip=True)         if title  else "",
                "body":   body.get_text(strip=True)          if body   else "",
                "rating": rating.get("data-service-review-rating", "") if rating else "",
                "date":   date.get("datetime", "")           if date   else "",
            })

        return reviews

    except Exception as e:
        logger.error(f"[reviews_monitor] Trustpilot scrape failed for {url}: {e}")
        return []


async def _fetch_capterra_reviews(url: str) -> list[dict]:
    """
    Scrape recent reviews from a Capterra product page.
    """
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=SCRAPER_TIMEOUT_SECS, follow_redirects=True) as client:
            r = await client.get(url)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")

        reviews = []
        cards = soup.select("div[data-testid='review-card']")

        for card in cards[:20]:
            title  = card.select_one("h3")
            body   = card.select_one("div[class*='review-body']")
            rating = card.select_one("span[class*='rating']")
            date   = card.select_one("time")

            reviews.append({
                "title":  title.get_text(strip=True)  if title  else "",
                "body":   body.get_text(strip=True)   if body   else "",
                "rating": rating.get_text(strip=True) if rating else "",
                "date":   date.get_text(strip=True)   if date   else "",
            })

        return reviews

    except Exception as e:
        logger.error(f"[reviews_monitor] Capterra scrape failed for {url}: {e}")
        return []


async def _fetch_producthunt_reviews(url: str) -> list[dict]:
    """
    Scrape recent reviews/comments from a Product Hunt product page.
    """
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=SCRAPER_TIMEOUT_SECS, follow_redirects=True) as client:
            r = await client.get(url)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")

        reviews = []
        cards = soup.select("div[data-test='review']")

        for card in cards[:20]:
            body   = card.select_one("div[class*='styles_htmlText']")
            rating = card.select_one("img[alt*='star']")
            date   = card.select_one("time")

            reviews.append({
                "title":  "",
                "body":   body.get_text(strip=True)        if body   else "",
                "rating": rating.get("alt", "")            if rating else "",
                "date":   date.get("datetime", "")         if date   else "",
            })

        return reviews

    except Exception as e:
        logger.error(f"[reviews_monitor] ProductHunt scrape failed for {url}: {e}")
        return []


# ─── Generic Fallback ─────────────────────────────────────────────────────────

async def _fetch_generic_reviews(url: str) -> list[dict]:
    """
    Generic fallback for unrecognized review pages.
    Extracts any blockquote or review-like text blocks.
    """
    try:
        async with httpx.AsyncClient(headers=HEADERS, timeout=SCRAPER_TIMEOUT_SECS, follow_redirects=True) as client:
            r = await client.get(url)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")

        reviews = []
        candidates = soup.select("blockquote, [class*='review'], [class*='testimonial']")

        for el in candidates[:20]:
            body = el.get_text(strip=True)
            if len(body) > 20:
                reviews.append({"title": "", "body": body[:300], "rating": "", "date": ""})

        return reviews

    except Exception as e:
        logger.error(f"[reviews_monitor] Generic scrape failed for {url}: {e}")
        return []


# ─── Router ───────────────────────────────────────────────────────────────────

async def fetch_reviews(url: str) -> list[dict]:
    platform = _detect_platform(url)
    if platform == "g2":
        return await _fetch_g2_reviews(url)
    if platform == "trustpilot":
        return await _fetch_trustpilot_reviews(url)
    if platform == "capterra":
        return await _fetch_capterra_reviews(url)
    if platform == "producthunt":
        return await _fetch_producthunt_reviews(url)
    return await _fetch_generic_reviews(url)


def _reviews_to_text(reviews: list[dict]) -> str:
    """Serialize review list to a stable string for hashing and diffing."""
    lines = []
    for r in reviews:
        lines.append(f"[{r['rating']}] {r['title']} — {r['body'][:200]}")
    return "\n".join(lines)


def _summarize_new_reviews(old_text: str, new_reviews: list[dict]) -> str:
    """
    Build a human-readable summary of reviews that appear
    to be new compared to the old snapshot.
    """
    lines = [f"📝 {len(new_reviews)} review(s) detected on latest check:\n"]
    for r in new_reviews[:5]:
        stars  = f"⭐ {r['rating']}" if r["rating"] else ""
        title  = r["title"] or "Untitled"
        body   = r["body"][:150] + "..." if len(r["body"]) > 150 else r["body"]
        lines.append(f"{stars} <b>{title}</b>\n{body}\n")
    return "\n".join(lines)


# ─── Main Runner ──────────────────────────────────────────────────────────────

async def run_reviews_monitor(bot):
    """
    Check all active reviews watches that are due based on tier interval.
    Called by the scheduler every hour.
    """
    changes_detected = 0

    for tier, config in TIER_LIMITS.items():
        interval_hrs = config["check_interval_hrs"]
        due_watches  = get_watches_due(interval_hrs)

        review_watches = [
            w for w in due_watches
            if w["watch_type"] == "reviews" and w["tier"] == tier
        ]

        if not review_watches:
            continue

        logger.info(f"[reviews_monitor] Checking {len(review_watches)} review watches for tier={tier}")

        for watch in review_watches:
            watch_id   = watch["id"]
            user_id    = watch["user_id"]
            label      = watch["label"]
            url        = watch["url"]
            last_hash  = watch["last_hash"]
            tier_cfg   = TIER_LIMITS.get(tier, TIER_LIMITS["free"])
            ai_enabled = tier_cfg["ai_summary"]

            try:
                reviews = await fetch_reviews(url)
                if not reviews:
                    logger.warning(f"[reviews_monitor] No reviews returned for {url}")
                    continue

                reviews_text = _reviews_to_text(reviews)

                # First run — store baseline
                if last_hash is None:
                    update_watch_hash(watch_id, hash_content(reviews_text))
                    logger.info(f"[reviews_monitor] Baseline stored for {label}")
                    continue

                if not has_changed(reviews_text, last_hash):
                    update_watch_hash(watch_id, last_hash)
                    continue

                summary = _summarize_new_reviews("", reviews)

                ai_summary = None
                if ai_enabled:
                    ai_summary = await analyse_change(
                        label        = label,
                        url          = url,
                        watch_type   = "reviews",
                        summary_diff = reviews_text[:800],
                    )

                change_id = log_change(
                    watch_id     = watch_id,
                    user_id      = user_id,
                    old_snapshot = truncate_snapshot(last_hash),
                    new_snapshot = truncate_snapshot(reviews_text),
                    ai_summary   = ai_summary,
                )

                update_watch_changed(watch_id, hash_content(reviews_text))

                sent = await send_alert(
                    bot          = bot,
                    user_id      = user_id,
                    label        = label,
                    url          = url,
                    watch_type   = "reviews",
                    summary_diff = summary,
                    ai_summary   = ai_summary,
                )

                if sent:
                    mark_notified(change_id)
                    changes_detected += 1

            except Exception as e:
                logger.error(f"[reviews_monitor] Error processing watch {watch_id}: {e}")

    logger.info(f"[reviews_monitor] Run complete — {changes_detected} change(s) detected")
    return changes_detected
