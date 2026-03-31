# services/ai_analyst.py
import httpx
import logging
from config import (
    OPENROUTER_API_KEY,
    OPENROUTER_MODEL,
    OPENROUTER_URL,
    AI_ANALYST_PROMPT,
)

logger = logging.getLogger(__name__)


async def analyse_change(
    label: str,
    url: str,
    watch_type: str,
    summary_diff: str,
    price_hits: list[str] = None,
    link_diff: dict = None,
) -> str | None:
    """
    Send the diff to OpenRouter and get a competitive intelligence summary.
    Returns the AI response string, or None on failure.
    """
    before_block, after_block = _build_diff_blocks(
        summary_diff, price_hits, link_diff, watch_type
    )

    prompt = AI_ANALYST_PROMPT.format(
        before=before_block,
        after=after_block,
    )

    system = (
        f"You are analysing changes to '{label}' ({url}). "
        f"Watch type: {watch_type}. Be concise, strategic, and direct."
    )

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                OPENROUTER_URL,
                headers={
                    "Authorization":  f"Bearer {OPENROUTER_API_KEY}",
                    "Content-Type":   "application/json",
                    "HTTP-Referer":   "https://competitorbot",
                    "X-Title":        "CompetitorBot",
                },
                json={
                    "model": OPENROUTER_MODEL,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user",   "content": prompt},
                    ],
                    "max_tokens":   250,
                    "temperature":  0.4,
                },
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"].strip()

    except httpx.HTTPStatusError as e:
        logger.error(f"OpenRouter HTTP error: {e.response.status_code} — {e.response.text}")
    except httpx.TimeoutException:
        logger.error("OpenRouter request timed out")
    except Exception as e:
        logger.error(f"OpenRouter unexpected error: {e}")

    return None


def _build_diff_blocks(
    summary_diff: str,
    price_hits: list[str],
    link_diff: dict,
    watch_type: str,
) -> tuple[str, str]:
    """
    Build human-readable before/after blocks to inject into the prompt.
    Tailored by watch_type for tighter, cheaper prompts.
    """
    if watch_type == "jobs":
        added   = link_diff.get("added", [])   if link_diff else []
        removed = link_diff.get("removed", []) if link_diff else []
        before  = f"Job postings removed:\n" + ("\n".join(removed) if removed else "None")
        after   = f"Job postings added:\n"   + ("\n".join(added)   if added   else "None")
        return before, after

    if watch_type == "page" and price_hits:
        before = f"Pricing lines detected in new content:\n" + "\n".join(price_hits[:10])
        after  = f"Full diff summary:\n{summary_diff[:600]}"
        return before, after

    # Default — reviews or generic page diff
    before = summary_diff[:400] if summary_diff else "No previous snapshot."
    after  = summary_diff[400:800] if len(summary_diff) > 400 else "See above."
    return before, after
