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

IDEMPOTENCY: `client.actor(ACTOR_ID).start(...)` is a real, billable, non-
idempotent call. This function claims the job's PENDING -> SCRAPING
transition atomically in Postgres (`db.claim_job`) BEFORE calling it, so at
most one invocation of this function can ever reach that call for a given
job -- no matter how many times QStash redelivers the message. See
db.claim_job's docstring for why the claim must happen before, not after.
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


def _ensure_poll_scheduled(job_id: str) -> None:
    queue.publish(
        "scrape_poll",
        {"job_id": job_id, "attempt": 0},
        delay="10s",
        dedup_id=f"poll-{job_id}-0",
    )


def run(payload: Dict[str, Any]) -> Dict[str, Any]:
    job_id = payload.get("job_id")
    if not job_id:
        raise TerminalError("Missing job_id in payload.")

    job = db.get_job(job_id)
    if not job:
        raise TerminalError(f"Job {job_id} not found.")

    # Atomically claim the PENDING -> SCRAPING transition BEFORE calling
    # Apify. Only one invocation of this function, ever, can win this
    # compare-and-swap for a given job -- every other invocation (a QStash
    # retry, a genuine duplicate delivery, or two overlapping deliveries)
    # loses it and returns below without calling Apify at all.
    claimed = db.claim_job(
        job_id,
        expect_status=JobStatus.PENDING,
        set_status=JobStatus.SCRAPING,
        scrape_started_at=now_iso(),
    )
    if claimed is None:
        current = db.get_job(job_id) or {}
        if current.get("status") == JobStatus.SCRAPING and current.get("apify_run_id"):
            # A previous invocation already won the claim and successfully
            # started the run. Re-ensuring the poll is scheduled is itself
            # idempotent (QStash deduplication_id), so this is safe to repeat.
            _ensure_poll_scheduled(job_id)
            return {"already_started": True}
        # Either another invocation currently owns this job's transition and
        # hasn't finished yet, or the job already moved past scraping
        # (analyzing/completed/failed/deleted). Nothing to do either way --
        # in particular, never fall through to calling Apify.
        return {"skipped": True, "job_status": current.get("status")}

    # We now atomically OWN this job's PENDING -> SCRAPING transition. No
    # other invocation of this function, present or future, can reach this
    # point for this job -- it is safe to call Apify exactly once below.
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

    # apify-client >=3.x returns a typed `Run` object here, not a dict --
    # `.id` (attribute access), not `.get("id")`. Confirmed against the
    # installed apify-client 3.1.3's Run pydantic model; using dict-style
    # .get() on it raises AttributeError (this was the actual production
    # bug: the crash landed AFTER the actor run had already started, so the
    # run id was never saved and every QStash retry started another one).
    run_id = started.id
    if not run_id:
        db.fail_job(job_id, "Apify did not return a run id.")
        raise TerminalError("Apify did not return a run id.")

    try:
        db.update_job(job_id, apify_run_id=run_id, updated_at=now_iso())
    except Exception as exc:  # noqa: BLE001
        # The run genuinely started (billable) but we could not record its id.
        # The job is already past PENDING, so no retry of this function can
        # ever reach client.actor(...).start() again for this job -- that
        # guarantee holds regardless of this failure. But without a saved
        # run_id nothing will ever poll it, so surface a clear, non-retrying
        # failure (including the run id) rather than leaving the job silently
        # stuck at "scraping" forever.
        db.fail_job(
            job_id,
            f"Apify run {run_id} started but its ID could not be saved ({exc}). "
            "Check this run in your Apify dashboard -- it may have completed "
            "with no further action taken here.",
        )
        raise TerminalError(f"Failed to persist apify_run_id: {exc}") from exc

    _ensure_poll_scheduled(job_id)
    print(f"Agent 1: Actor run {run_id} started; polling scheduled.")
    return {"apify_run_id": run_id}


