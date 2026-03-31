# services/differ.py
import hashlib
import difflib
import logging
import re
from config import MAX_SNAPSHOT_LEN

logger = logging.getLogger(__name__)


def hash_content(text: str) -> str:
    """SHA-256 hash of normalized text — case insensitive, whitespace collapsed."""
    normalized = re.sub(r"\s+", " ", text.lower().strip())
    return hashlib.sha256(normalized.encode()).hexdigest()


def hash_meta(meta: dict) -> str:
    """Hash all meta fields combined — catches headline/description changes."""
    combined = "|".join([
        meta.get("title", ""),
        meta.get("description", ""),
        meta.get("og_title", ""),
        meta.get("og_desc", ""),
    ]).lower()
    return hashlib.sha256(combined.encode()).hexdigest()


def hash_pricing(pricing: dict) -> str:
    """Hash pricing signals — catches plan name and price changes."""
    combined = "|".join(
        sorted(pricing.get("prices", []) + pricing.get("plan_names", []))
    ).lower()
    return hashlib.sha256(combined.encode()).hexdigest()


def has_changed(new_text: str, last_hash: str | None) -> bool:
    """Returns False on first run (no stored hash) to prevent false alerts."""
    if last_hash is None:
        return False
    return hash_content(new_text) != last_hash


def get_diff(old_text: str, new_text: str, context_lines: int = 3) -> str:
    """
    Line-by-line unified diff.
    Works correctly because extract_text() now preserves line structure.
    """
    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)
    diff      = list(difflib.unified_diff(
        old_lines,
        new_lines,
        fromfile = "before",
        tofile   = "after",
        n        = context_lines,
    ))
    return "".join(diff) if diff else ""


def get_summary_diff(old_text: str, new_text: str, max_chars: int = 600) -> str:
    """
    Clean human-readable diff — strips diff header lines,
    keeps only added/removed lines for the alert message.
    """
    diff = get_diff(old_text, new_text)
    if not diff:
        return "Minor formatting change detected."

    # Keep only +/- lines, strip unified diff header
    meaningful = []
    for line in diff.splitlines():
        if line.startswith(("+++", "---", "@@")):
            continue
        if line.startswith(("+", "-")) and line.strip() not in ("+", "-"):
            meaningful.append(line[:200])

    result = "\n".join(meaningful[:30])
    return result[:max_chars] + ("..." if len(result) > max_chars else "")


def diff_meta(old_meta: dict, new_meta: dict) -> list[str]:
    """
    Return list of human-readable meta field changes.
    Empty list means no meta changes.
    """
    changes = []
    fields  = {
        "title":       "Page title",
        "description": "Meta description",
        "og_title":    "OG title",
        "og_desc":     "OG description",
    }
    for key, label in fields.items():
        old_val = old_meta.get(key, "")
        new_val = new_meta.get(key, "")
        if old_val != new_val and (old_val or new_val):
            changes.append(f"{label}: '{old_val[:80]}' → '{new_val[:80]}'")
    return changes


def diff_pricing(old_pricing: dict, new_pricing: dict) -> list[str]:
    """
    Return list of human-readable pricing changes.
    """
    changes   = []
    old_prices = set(old_pricing.get("prices", []))
    new_prices = set(new_pricing.get("prices", []))
    old_plans  = set(old_pricing.get("plan_names", []))
    new_plans  = set(new_pricing.get("plan_names", []))

    for p in new_prices - old_prices:
        changes.append(f"💰 New price detected: {p}")
    for p in old_prices - new_prices:
        changes.append(f"💰 Price removed: {p}")
    for p in new_plans - old_plans:
        changes.append(f"📦 New plan name: {p}")
    for p in old_plans - new_plans:
        changes.append(f"📦 Plan removed: {p}")

    return changes


def truncate_snapshot(text: str) -> str:
    return text[:MAX_SNAPSHOT_LEN]


def extract_price_candidates(text: str) -> list[str]:
    """Regex fallback for price detection in raw text."""
    price_pattern = re.compile(
        r"(\$[\d,]+(?:\.\d{2})?|€[\d,]+|£[\d,]+|per\s+month|\/mo|\/year|free)",
        re.IGNORECASE,
    )
    lines = [line.strip() for line in text.splitlines() if price_pattern.search(line) and line.strip()]
    return lines[:20]


def diff_link_lists(old_links: list[str], new_links: list[str]) -> dict:
    old_set = set(old_links)
    new_set = set(new_links)
    return {
        "added":   list(new_set - old_set),
        "removed": list(old_set - new_set),
    }


def build_change_payload(
    old_text:    str,
    new_text:    str,
    old_links:   list[str] = None,
    new_links:   list[str] = None,
    old_meta:    dict = None,
    new_meta:    dict = None,
    old_pricing: dict = None,
    new_pricing: dict = None,
) -> dict:
    """
    Master payload builder — covers text, meta, pricing, and link diffs.
    """
    new_hash      = hash_content(new_text)
    summary_diff  = get_summary_diff(old_text, new_text)
    price_hits    = extract_price_candidates(new_text)
    link_diff     = diff_link_lists(old_links or [], new_links or [])
    meta_changes  = diff_meta(old_meta or {}, new_meta or {}) if (old_meta or new_meta) else []
    price_changes = diff_pricing(old_pricing or {}, new_pricing or {}) if (old_pricing or new_pricing) else []

    return {
        "new_hash":      new_hash,
        "old_snapshot":  truncate_snapshot(old_text),
        "new_snapshot":  truncate_snapshot(new_text),
        "summary_diff":  summary_diff,
        "price_hits":    price_hits,
        "link_diff":     link_diff,
        "meta_changes":  meta_changes,
        "price_changes": price_changes,
    }