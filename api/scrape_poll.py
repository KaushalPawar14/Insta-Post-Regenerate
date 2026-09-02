"""
Agent 1 (part 2 of 2): poll the Apify run, then fan out one analyze message
per post.

This is where the original `agent_1_scraper.py` field extraction and
sort-by-likes live. The extraction is deliberately tolerant, because
single-post mode has been confirmed against Apify's documentation but not yet
against real actor output -- see `normalise_item()`.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from typing import Any, Dict, List, Optional  # noqa: E402

from apify_client import ApifyClient  # noqa: E402

from _lib import config, db, queue  # noqa: E402
from _lib.handler import TerminalError, make_handler  # noqa: E402
from _lib.pipeline import now_iso  # noqa: E402
from _lib.schemas import JobStatus, PostStatus  # noqa: E402

# ~15s x 40 = up to 10 minutes of scraping before we give up. Each poll is a
# separate short invocation, so none of this counts against the 300s ceiling.
MAX_ATTEMPTS = 40
POLL_DELAY = "15s"

TERMINAL_FAILURE_STATES = {"FAILED", "ABORTED", "TIMED-OUT", "TIMING-OUT"}
IN_PROGRESS_STATES = {"READY", "RUNNING"}


def _first(item: Dict[str, Any], *keys: str) -> Optional[Any]:
    """Return the first key present with a truthy value."""
    for key in keys:
        value = item.get(key)
        if value:
            return value
    return None


def normalise_item(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Map one Apify dataset item onto the fields the original pipeline used.

    The canonical field names (`id`, `likesCount`, `commentsCount`, `caption`,
    `displayUrl`) are tried first, exactly as in `agent_1_scraper.py`.
    Fallbacks exist because we have not yet validated single-post-mode output
    against a live run; if the actor names a field differently there, this
    degrades instead of producing an empty post.

    Returns None for items that are not usable posts -- the actor can emit
    profile/detail objects alongside posts, and a post with no image is
    useless to the Analyzer.
    """
    thumbnail = _first(item, "displayUrl", "thumbnailUrl", "imageUrl", "displayUrlOriginal")
    if not thumbnail:
        images = item.get("images")
        if isinstance(images, list) and images:
            first_image = images[0]
            thumbnail = first_image if isinstance(first_image, str) else (
                first_image.get("url") if isinstance(first_image, dict) else None
            )
    if not thumbnail:
        return None

    post_id = _first(item, "id", "shortCode", "shortcode", "postId") or "unknown_id"

    likes = item.get("likesCount")
    if likes is None:
        likes = item.get("likes")
    try:
        likes = int(likes)
    except (TypeError, ValueError):
        likes = 0
    # Instagram returns -1 when the owner has hidden the like count. Left
    # as-is it would corrupt the sort, so it is treated as unknown/zero.
    if likes < 0:
        likes = 0

    comments = item.get("commentsCount")
    if comments is None:
        comments = item.get("comments")
    if isinstance(comments, list):
        comments = len(comments)
    try:
        comments = int(comments)
    except (TypeError, ValueError):
        comments = 0
    if comments < 0:
        comments = 0

    return {
        "id": str(post_id),
        "likes": likes,
        "comments": comments,
        "caption": item.get("caption") or "",
        "thumbnail_url": str(thumbnail),
    }


def run(payload: Dict[str, Any]) -> Dict[str, Any]:
    job_id = payload.get("job_id")
    attempt = int(payload.get("attempt") or 0)
    if not job_id:
        raise TerminalError("Missing job_id in payload.")

    job = db.get_job(job_id)
    if not job:
        raise TerminalError(f"Job {job_id} not found.")

    if job.get("status") not in (JobStatus.SCRAPING, JobStatus.PENDING):
        # Already progressed past scraping (or was deleted/failed).
        return {"skipped": True, "job_status": job.get("status")}

    run_id = job.get("apify_run_id")
    if not run_id:
        db.fail_job(job_id, "Job has no Apify run id to poll.")
        raise TerminalError("Job has no Apify run id.")

    client = ApifyClient(config.apify_token())
    actor_run = client.run(run_id).get() or {}
    state = (actor_run.get("status") or "").upper()

    if state in IN_PROGRESS_STATES:
        if attempt >= MAX_ATTEMPTS:
            db.fail_job(
                job_id,
                f"Apify run {run_id} did not finish within ~10 minutes "
                f"(last state: {state}).",
            )
            raise TerminalError("Apify run timed out.")
        queue.publish(
            "scrape_poll",
            {"job_id": job_id, "attempt": attempt + 1},
            delay=POLL_DELAY,
            dedup_id=f"poll-{job_id}-{attempt + 1}",
        )
        return {"state": state, "attempt": attempt, "rescheduled": True}

    if state in TERMINAL_FAILURE_STATES:
        db.fail_job(job_id, f"Apify run {run_id} finished with status {state}.")
        raise TerminalError(f"Apify run {state}.")

    if state != "SUCCEEDED":
        db.fail_job(job_id, f"Apify run {run_id} returned unexpected status {state!r}.")
        raise TerminalError(f"Unexpected Apify status {state!r}.")

    # --- run succeeded: read the dataset -----------------------------------
    dataset_id = actor_run.get("defaultDatasetId")
    if not dataset_id:
        db.fail_job(job_id, "Apify run succeeded but exposed no dataset.")
        raise TerminalError("No dataset on the Apify run.")

    raw_scraped_data: List[Dict[str, Any]] = []
    for item in client.dataset(dataset_id).iterate_items():
        normalised = normalise_item(item)
        if normalised:
            raw_scraped_data.append(normalised)

    if not raw_scraped_data:
        db.fail_job(
            job_id,
            "Apify returned no usable posts. The profile may be private or "
            "empty, the post URL may be invalid, or Instagram may have "
            "blocked the request. Check the URL and try again.",
        )
        raise TerminalError("Apify returned no items.")

    input_type = job.get("input_type") or "profile"
    if input_type == "post":
        # One post: nothing to rank.
        selected = raw_scraped_data[:1]
    else:
        # Unchanged from the original: sort descending by likes, then take the
        # user's requested count (capped server-side).
        target_count = min(int(job.get("max_posts") or 1), config.MAX_POSTS_CEILING)
        selected = sorted(raw_scraped_data, key=lambda x: x["likes"], reverse=True)[:target_count]

    rows = [
        {
            "job_id": job_id,
            "user_id": job["user_id"],
            "post_id": item["id"],
            "likes": item["likes"],
            "comments": item["comments"],
            "original_caption": item["caption"],
            "raw_image_url": item["thumbnail_url"],
            "rank": index,
            "status": PostStatus.PENDING,
        }
        for index, item in enumerate(selected)
    ]
    inserted = db.insert_posts(rows)

    db.update_job(
        job_id,
        status=JobStatus.ANALYZING,
        total_posts=len(inserted),
        scrape_completed_at=now_iso(),
        updated_at=now_iso(),
    )

    # Fan out: one analyze message per post. This replaces the LangGraph
    # Scraper -> Analyzer edge.
    for row in inserted:
        queue.publish(
            "analyze",
            {"post_row_id": row["id"]},
            retries=2,
            dedup_id=f"analyze-{row['id']}",
        )

    print(f"Agent 1: Successfully secured top {len(inserted)} posts and updated state.")
    return {"posts": len(inserted)}


handler = make_handler("scrape_poll", run)
