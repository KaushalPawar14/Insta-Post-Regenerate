"""
Agent 1 (part 1 of 2): start the Apify scrape.

Ported from `nodes/agent_1_scraper.py`. The Apify integration is unchanged --
same client, same `apify/instagram-scraper` actor, same extracted fields, same
sort-by-likes. Two things differ, both forced by the new execution model:

1. The actor run is STARTED here and polled by `scrape_poll.py`, rather than
   blocking on `.call()`. A 100-post scrape can outlast Vercel's hard 300s
   function ceiling; starting and polling removes that risk entirely.

2. A single post URL skips scraping-by-profile: the same actor is given the
   post URL directly via `directUrls` with `resultsLimit` 1. Sorting is
   meaningless for one post, so it is skipped.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from typing import Any, Dict  # noqa: E402

from apify_client import ApifyClient  # noqa: E402

from _lib import config, db, queue  # noqa: E402
from _lib.handler import TerminalError
from _lib.pipeline import now_iso  # noqa: E402
from _lib.schemas import JobStatus  # noqa: E402

ACTOR_ID = "apify/instagram-scraper"


def run(payload: Dict[str, Any]) -> Dict[str, Any]:
    job_id = payload.get("job_id")
    if not job_id:
        raise TerminalError("Missing job_id in payload.")

    job = db.get_job(job_id)
    if not job:
        raise TerminalError(f"Job {job_id} not found.")

    if job.get("apify_run_id"):
        # Already started -- a duplicate delivery. Do not start a second
        # (billable) actor run; just make sure polling is scheduled.
        queue.publish(
            "scrape_poll",
            {"job_id": job_id, "attempt": 0},
            delay="10s",
            dedup_id=f"poll-{job_id}-0",
        )
        return {"already_started": True}

    target_url = job["input_url"]
    input_type = job.get("input_type") or "profile"

    # Server-side ceiling, enforced again here so a request that bypassed the
    # Next.js route entirely still cannot exceed it.
    target_count = min(int(job.get("max_posts") or 1), config.MAX_POSTS_CEILING)

    print(f"\nAgent 1: Initializing Apify scraper for {target_url}...")
    print(f"Agent 1: Target post count set to {target_count}.")

    client = ApifyClient(config.apify_token())

    if input_type == "post":
        # Single post / reel: the same actor accepts a direct /p/ or /reel/
        # URL. `resultsLimit` is 1 because a post URL yields exactly one item.
        run_input = {
            "directUrls": [target_url],
            "resultsLimit": 1,
            "resultsType": "posts",
        }
    else:
        # Unchanged from the original pipeline.
        run_input = {
            "directUrls": [target_url],
            "resultsLimit": target_count,
            "resultsType": "posts",
        }

    print(f"Agent 1: Starting Apify Actor ({ACTOR_ID})...")
    try:
        started = client.actor(ACTOR_ID).start(run_input=run_input)
    except Exception as exc:  # noqa: BLE001
        db.fail_job(job_id, f"Could not start the Apify actor: {exc}")
        raise TerminalError(str(exc)) from exc

    run_id = started.get("id")
    if not run_id:
        db.fail_job(job_id, "Apify did not return a run id.")
        raise TerminalError("Apify did not return a run id.")

    db.update_job(
        job_id,
        status=JobStatus.SCRAPING,
        apify_run_id=run_id,
        scrape_started_at=now_iso(),
        updated_at=now_iso(),
    )

    queue.publish(
        "scrape_poll",
        {"job_id": job_id, "attempt": 0},
        delay="10s",
        dedup_id=f"poll-{job_id}-0",
    )
    print(f"Agent 1: Actor run {run_id} started; polling scheduled.")
    return {"apify_run_id": run_id}


