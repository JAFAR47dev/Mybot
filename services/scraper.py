# services/scraper.py
import httpx
import asyncio
import random
import logging
import feedparser
from bs4 import BeautifulSoup
from config import (
    SCRAPER_TIMEOUT_SECS,
    SCRAPER_USER_AGENT,
    SCRAPER_MIN_DELAY_SECS,
    SCRAPER_MAX_DELAY_SECS,
)

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent":                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept":                    "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language":           "en-US,en;q=0.5",
    "Accept-Encoding":           "gzip, deflate, br",
    "DNT":                       "1",
    "Connection":                "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

MAX_RETRIES = 3


async def random_delay():
    await asyncio.sleep(random.uniform(SCRAPER_MIN_DELAY_SECS, SCRAPER_MAX_DELAY_SECS))


async def fetch_page(url: str, retries: int = MAX_RETRIES) -> str | None:
    """
    Fetch raw HTML with retry logic.
    Returns None only after all retries exhausted.
    """
    for attempt in range(1, retries + 1):
        try:
            await random_delay()
            async with httpx.AsyncClient(
                headers          = HEADERS,
                timeout          = SCRAPER_TIMEOUT_SECS,
                follow_redirects = True,
            ) as client:
                response = await client.get(url)
                response.raise_for_status()
                return response.text
        except httpx.HTTPStatusError as e:
            logger.warning(f"[scraper] HTTP {e.response.status_code} for {url} (attempt {attempt}/{retries})")
            if e.response.status_code in (403, 404, 410):
                break  # no point retrying permanent errors
        except httpx.TimeoutException:
            logger.warning(f"[scraper] Timeout for {url} (attempt {attempt}/{retries})")
        except Exception as e:
            logger.error(f"[scraper] Unexpected error for {url}: {e}")
        if attempt < retries:
            await asyncio.sleep(attempt * 3)  # backoff: 3s, 6s
    logger.error(f"[scraper] All {retries} attempts failed for {url}")
    return None


def extract_text(html: str) -> str:
    """
    Extract visible page text.
    Keeps nav and header text — competitor menu/nav changes are high signal.
    Only removes scripts, styles, and hidden elements.
    """
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "head",
                     "meta", "link", "svg", "img"]):
        tag.decompose()
    text  = soup.get_text(separator="\n")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)   # preserve line structure for diffing


def extract_meta(html: str) -> dict:
    """
    Extract meta signals — title, description, og:title, og:description,
    canonical URL, and structured pricing schema if present.
    """
    soup        = BeautifulSoup(html, "html.parser")
    title       = soup.title.string.strip() if soup.title else ""
    description = ""
    og_title    = ""
    og_desc     = ""
    canonical   = ""

    for tag in soup.find_all("meta"):
        name    = tag.get("name", "").lower()
        prop    = tag.get("property", "").lower()
        content = tag.get("content", "").strip()
        if name == "description":
            description = content
        if prop == "og:title":
            og_title = content
        if prop == "og:description":
            og_desc = content

    canonical_tag = soup.find("link", rel="canonical")
    if canonical_tag:
        canonical = canonical_tag.get("href", "")

    return {
        "title":       title,
        "description": description,
        "og_title":    og_title,
        "og_desc":     og_desc,
        "canonical":   canonical,
    }


def extract_links(html: str, base_url: str = "") -> list[str]:
    """Extract all href links — preserves full URLs and resolves relative ones."""
    soup  = BeautifulSoup(html, "html.parser")
    links = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        if href.startswith("http"):
            links.append(href)
        elif href.startswith("/") and base_url:
            from urllib.parse import urlparse
            parsed = urlparse(base_url)
            links.append(f"{parsed.scheme}://{parsed.netloc}{href}")
    return list(set(links))


def extract_pricing_signals(html: str) -> dict:
    """
    Dedicated pricing extractor — captures pricing tables,
    plan names, price amounts, and CTA buttons.
    Much more reliable than regex on raw text.
    """
    import re
    soup   = BeautifulSoup(html, "html.parser")
    result = {
        "prices":     [],
        "plan_names": [],
        "ctas":       [],
    }

    # Price amounts — $X, €X, £X, Xmo, X/year
    price_re = re.compile(
        r"(\$|€|£|USD|EUR)\s?[\d,]+(?:\.\d{2})?|[\d,]+\s?(?:\/mo|\/month|\/year|per month)",
        re.IGNORECASE,
    )
    for tag in soup.find_all(string=price_re):
        text = tag.strip()
        if text:
            result["prices"].append(text[:100])

    # Plan/tier names — common patterns
    plan_re = re.compile(
        r"\b(free|starter|basic|pro|professional|business|enterprise|premium|team|agency|plus|growth)\b",
        re.IGNORECASE,
    )
    for tag in soup.find_all(string=plan_re):
        parent = tag.parent
        if parent and parent.name in ("h1", "h2", "h3", "h4", "span", "div", "p", "li"):
            text = tag.strip()
            if 2 < len(text) < 80:
                result["plan_names"].append(text)

    # CTA buttons
    for btn in soup.find_all(["button", "a"], string=True):
        text = btn.get_text(strip=True)
        if any(kw in text.lower() for kw in ["get started", "try free", "sign up", "buy", "subscribe", "start"]):
            result["ctas"].append(text[:60])

    # Deduplicate
    result["prices"]     = list(dict.fromkeys(result["prices"]))[:10]
    result["plan_names"] = list(dict.fromkeys(result["plan_names"]))[:10]
    result["ctas"]       = list(dict.fromkeys(result["ctas"]))[:5]

    return result


def extract_changelog_signals(html: str) -> list[str]:
    """
    Extract changelog/blog post titles and dates.
    Used for changelog watch type.
    """
    soup    = BeautifulSoup(html, "html.parser")
    entries = []

    # Common blog/changelog article patterns
    for tag in soup.find_all(["h1", "h2", "h3", "article", "li"]):
        text = tag.get_text(strip=True)
        if 10 < len(text) < 200:
            entries.append(text)

    return entries[:30]


async def fetch_rss(url: str) -> list[dict]:
    """
    Fetch and parse an RSS/Atom feed.
    Returns list of {title, link, published} dicts.
    """
    try:
        html = await fetch_page(url)
        if not html:
            return []
        feed    = feedparser.parse(html)
        entries = []
        for entry in feed.entries[:20]:
            entries.append({
                "title":     entry.get("title", ""),
                "link":      entry.get("link", ""),
                "published": entry.get("published", ""),
            })
        return entries
    except Exception as e:
        logger.error(f"[scraper] RSS fetch failed for {url}: {e}")
        return []


async def fetch_and_extract(url: str) -> dict | None:
    """
    Full pipeline: fetch → extract all signals.
    Returns None if fetch fails after all retries.
    """
    html = await fetch_page(url)
    if not html:
        return None

    return {
        "text":     extract_text(html),
        "meta":     extract_meta(html),
        "links":    extract_links(html, base_url=url),
        "pricing":  extract_pricing_signals(html),
        "changelog": extract_changelog_signals(html),
    }
