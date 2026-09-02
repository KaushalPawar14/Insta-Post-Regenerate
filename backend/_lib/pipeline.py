"""Shared stage helpers: claiming work safely and keeping job status in sync."""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from . import db
from .schemas import JobStatus, PostStatus

# A stage is considered abandoned once it has been "running" for longer than
# Vercel's hard 300s ceiling plus a little slack -- at that point the function
# that claimed it is guaranteed to have been killed.
STALE_AFTER = timedelta(seconds=330)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_ts(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def is_stale(timestamp: Optional[str]) -> bool:
    parsed = _parse_ts(timestamp)
    if parsed is None:
        return True
    return datetime.now(timezone.utc) - parsed > STALE_AFTER


def claim_analysis(row_id: str) -> Optional[Dict[str, Any]]:
    """
    Claim a post for analysis.

    Recovers abandoned work: if a previous invocation claimed the post and was
    killed mid-flight, a later delivery re-claims it. Analysis is cheap (one
    vision call), so re-running it is preferable to leaving a post stuck.
    """
    row = db.claim_post(
        row_id,
        expect_status=PostStatus.PENDING,
        set_status=PostStatus.ANALYZING,
        analyze_started_at=now_iso(),
        error=None,
    )
    if row:
        return row

    current = db.get_post(row_id)
    if not current:
        return None

    retryable = {PostStatus.ANALYZING, PostStatus.FAILED_ANALYSIS}
    if current["status"] in retryable and is_stale(current.get("analyze_started_at")):
        return db.claim_post(
            row_id,
            expect_status=current["status"],
            set_status=PostStatus.ANALYZING,
            analyze_started_at=now_iso(),
            error=None,
        )
    return None


def claim_generation(row_id: str) -> Optional[Dict[str, Any]]:
    """
    Claim a post for image generation.

    Deliberately STRICTER than `claim_analysis`: it only ever claims a post
    that is sitting in `queued_for_generation`. Generation costs real money, so
    an automatic QStash retry must never re-trigger it. If a generation was
    abandoned, the post is marked `failed_generation` and the user gets an
    explicit Retry button instead.
    """
    row = db.claim_post(
        row_id,
        expect_status=PostStatus.QUEUED_FOR_GENERATION,
        set_status=PostStatus.GENERATING,
        generate_started_at=now_iso(),
        error=None,
    )
    if row:
        return row

    current = db.get_post(row_id)
    if not current:
        return None

    if current["status"] == PostStatus.GENERATING and is_stale(
        current.get("generate_started_at")
    ):
        db.fail_post(
            row_id,
            PostStatus.FAILED_GENERATION,
            "Image generation did not finish within the 300s function limit. "
            "Press Retry to try again.",
        )
    return None


def refresh_job_status(job_id: str) -> None:
    """Roll per-post statuses up into the job's headline status."""
    counts = db.count_posts_by_status(job_id)
    if not counts:
        return

    total = sum(counts.values())
    settled = (
        counts.get(PostStatus.COMPLETED, 0)
        + counts.get(PostStatus.FAILED_ANALYSIS, 0)
        + counts.get(PostStatus.FAILED_GENERATION, 0)
        # A removed post will never be processed further -- it's just as
        # "done" as completed/failed for the purpose of deciding whether the
        # job as a whole still has pipeline work left to do. Without this, a
        # job where every remaining post gets removed (none confirmed) would
        # never resolve out of the generic "analyzing" bucket below.
        + counts.get(PostStatus.REMOVED, 0)
    )
    in_flight = (
        counts.get(PostStatus.PENDING, 0)
        + counts.get(PostStatus.ANALYZING, 0)
        + counts.get(PostStatus.QUEUED_FOR_GENERATION, 0)
        + counts.get(PostStatus.GENERATING, 0)
    )

    if settled == total:
        status = JobStatus.COMPLETED
    elif in_flight == 0 and counts.get(PostStatus.AWAITING_CONFIRMATION, 0):
        # Everything the pipeline can do on its own is done; the rest is
        # blocked on the user pressing Confirm.
        status = JobStatus.AWAITING_CONFIRMATION
    else:
        status = JobStatus.ANALYZING

    db.update_job(job_id, status=status, updated_at=now_iso())
