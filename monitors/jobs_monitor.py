# monitors/jobs_monitor.py
import httpx
import logging
from database.models import (
    get_watches_due,
    update_watch_hash,
    update_watch_changed,
    log_change,
    mark_notified,
    get_last_snapshot,
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

GREENHOUSE_API = "https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
LEVER_API      = "https://api.lever.co/v0/postings/{slug}?mode=json"


def _detect_board(url: str) -> tuple | None:
    if "greenhouse.io" in url:
        return ("greenhouse", url.rstrip("/").split("/")[-1])
    if "lever.co" in url:
        return ("lever", url.rstrip("/").split("/")[-1])
    return None


async def _fetch_greenhouse_jobs(slug: str) -> list:
    try:
        async with httpx.AsyncClient(timeout=SCRAPER_TIMEOUT_SECS) as client:
            r = await client.get(GREENHOUSE_API.format(slug=slug))
            r.raise_for_status()
            return [job["title"] for job in r.json().get("jobs", [])]
    except Exception as e:
        logger.error(f"[jobs_monitor] Greenhouse fetch failed for {slug}: {e}")
        return []


async def _fetch_lever_jobs(slug: str) -> list:
    try:
        async with httpx.AsyncClient(timeout=SCRAPER_TIMEOUT_SECS) as client:
            r = await client.get(LEVER_API.format(slug=slug))
            r.raise_for_status()
            return [job["text"] for job in r.json()]
    except Exception as e:
        logger.error(f"[jobs_monitor] Lever fetch failed for {slug}: {e}")
        return []


async def _fetch_jobs(url: str) -> list:
    board = _detect_board(url)
    if board:
        board_type, slug = board
        if board_type == "greenhouse":
            return await _fetch_greenhouse_jobs(slug)
        if board_type == "lever":
            return await _fetch_lever_jobs(slug)

    from services.scraper import fetch_page, extract_links
    html = await fetch_page(url)
    if not html:
        return []
    links = extract_links(html, base_url=url)
    job_keywords = ["job", "career", "position", "role", "opening", "posting"]
    return [l for l in links if any(kw in l.lower() for kw in job_keywords)]


async def run_jobs_monitor(bot):
    changes_detected = 0

    for tier, config in TIER_LIMITS.items():
        interval_hrs = config["check_interval_hrs"]
        due_watches  = get_watches_due(interval_hrs)

        job_watches = [
            w for w in due_watches
            if w["watch_type"] == "jobs" and w["tier"] == tier
        ]

        if not job_watches:
            continue

        logger.info(f"[jobs_monitor] Checking {len(job_watches)} job watches for tier={tier}")

        for watch in job_watches:
            watch_id   = watch["id"]
            user_id    = watch["user_id"]
            label      = watch["label"]
            url        = watch["url"]
            last_hash  = watch["last_hash"]
            ai_enabled = TIER_LIMITS.get(tier, TIER_LIMITS["free"])["ai_summary"]

            try:
                current_jobs = await _fetch_jobs(url)
                if not current_jobs:
                    logger.warning(f"[jobs_monitor] No jobs returned for {url}")
                    continue

                jobs_text = "\n".join(sorted(current_jobs))

                # First run — store baseline
                if last_hash is None:
                    update_watch_hash(watch_id, hash_content(jobs_text))
                    logger.info(f"[jobs_monitor] Baseline stored for {label}")
                    continue

                if not has_changed(jobs_text, last_hash):
                    update_watch_hash(watch_id, last_hash)
                    continue

                # Real diff — compare against previous snapshot
                old_snapshot = get_last_snapshot(watch_id)
                old_jobs_text = old_snapshot["new_snapshot"] if old_snapshot else ""
                old_jobs = set(old_jobs_text.splitlines()) if old_jobs_text else set()
                new_jobs = set(current_jobs)

                link_diff = {
                    "added":   list(new_jobs - old_jobs),
                    "removed": list(old_jobs - new_jobs),
                }

                ai_summary = None
                if ai_enabled:
                    ai_summary = await analyse_change(
                        label        = label,
                        url          = url,
                        watch_type   = "jobs",
                        summary_diff = (
                            f"Added: {len(link_diff['added'])} | "
                            f"Removed: {len(link_diff['removed'])}"
                        ),
                        link_diff    = link_diff,
                    )

                change_id = log_change(
                    watch_id     = watch_id,
                    user_id      = user_id,
                    old_snapshot = truncate_snapshot(old_jobs_text),
                    new_snapshot = truncate_snapshot(jobs_text),
                    ai_summary   = ai_summary,
                )

                update_watch_changed(watch_id, hash_content(jobs_text))

                sent = await send_alert(
                    bot          = bot,
                    user_id      = user_id,
                    label        = label,
                    url          = url,
                    watch_type   = "jobs",
                    summary_diff = (
                        f"{len(link_diff['added'])} new posting(s), "
                        f"{len(link_diff['removed'])} removed."
                    ),
                    ai_summary   = ai_summary,
                    link_diff    = link_diff,
                )

                if sent:
                    mark_notified(change_id)
                    changes_detected += 1

            except Exception as e:
                logger.error(f"[jobs_monitor] Error processing watch {watch_id}: {e}")

    logger.info(f"[jobs_monitor] Run complete — {changes_detected} change(s) detected")
    return changes_detected
