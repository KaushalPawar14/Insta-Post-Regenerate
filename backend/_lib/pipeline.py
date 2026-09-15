"""Shared stage helpers: claiming work safely and keeping job status in sync."""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from . import db
from .schemas import BrandGenerationStatus, JobStatus, PostStatus

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


def claim_brand_generation(post_id: str, brand: str) -> Optional[Dict[str, Any]]:
    """
    Claim one (post, brand) generation.

    Mirrors the pre-multi-brand `claim_generation`, retargeted at
    `job_post_brands`: it only ever claims a row sitting in
    `queued_for_generation`, and recovers a row abandoned mid-generation by
    marking it `failed_generation` (so the user gets an explicit retry
    affordance for that ONE brand, rather than the whole post) instead of
    leaving it stuck forever. Generation costs real money, so an automatic
    QStash retry must never silently re-trigger it -- claiming is what makes
    that impossible.

    NOTE: there used to be a post-level `claim_generation` here, operating
    directly on job_posts. It's been removed rather than left in place
    unused (unlike the Apify-cost change's precedent of leaving unused code
    around) because leaving it would be actively WRONG under the new model,
    not just unused: job_posts.status is now a rollup computed by
    `refresh_post_status` below, not a value a generation stage claims and
    sets directly. A stray call to the old function would silently no-op in
    a confusing way rather than doing anything either correct or harmless.
    """
    row = db.claim_post_brand(
        post_id,
        brand,
        expect_status=BrandGenerationStatus.QUEUED_FOR_GENERATION,
        set_status=BrandGenerationStatus.GENERATING,
        generate_started_at=now_iso(),
        error=None,
    )
    if row:
        return row

    current = db.get_post_brand(post_id, brand)
    if not current:
        return None

    if current["status"] == BrandGenerationStatus.GENERATING and is_stale(
        current.get("generate_started_at")
    ):
        db.fail_post_brand(
            post_id,
            brand,
            "Image generation did not finish within the 300s function limit. "
            "Press Retry to try again.",
        )
    return None


def compute_post_rollup_status(brand_rows: list) -> str:
    """
    Pure function: given all of a post's job_post_brands rows, return the
    job_posts.status rollup value. job_posts.status keeps its EXACT existing
    enum -- this is what lets PostStepper, the stage-breakdown UI, and the
    ETA calculation stay completely unchanged.

        any row generating                        -> generating
        some (not all) rows still queued           -> generating
        all rows still queued                      -> queued_for_generation
        all rows terminal, >=1 completed           -> completed
        all rows terminal, none completed          -> failed_generation
    """
    if not brand_rows:
        return PostStatus.QUEUED_FOR_GENERATION
    statuses = [r["status"] for r in brand_rows]
    if any(s == BrandGenerationStatus.GENERATING for s in statuses):
        return PostStatus.GENERATING
    if any(s == BrandGenerationStatus.QUEUED_FOR_GENERATION for s in statuses):
        if all(s == BrandGenerationStatus.QUEUED_FOR_GENERATION for s in statuses):
            return PostStatus.QUEUED_FOR_GENERATION
        return PostStatus.GENERATING
    # every row is terminal (completed or failed_generation) at this point
    if any(s == BrandGenerationStatus.COMPLETED for s in statuses):
        return PostStatus.COMPLETED
    return PostStatus.FAILED_GENERATION


def refresh_post_status(post_id: str) -> str:
    """
    Roll this post's job_post_brands rows up into job_posts.status (and,
    on first entering `generating`, stamp generate_started_at; on reaching a
    terminal rollup state, stamp generate_completed_at -- both preserved so
    the existing ETA averaging in lib/eta.ts keeps working unmodified).

    Returns the computed status so callers (generate.py) can decide whether
    to run terminal-state side effects, like dropping the transient
    thumbnail once nothing is generating anymore.
    """
    brand_rows = db.list_post_brands(post_id)
    status = compute_post_rollup_status(brand_rows)

    fields: Dict[str, Any] = {"status": status}
    post = db.get_post(post_id) or {}
    if status == PostStatus.GENERATING and not post.get("generate_started_at"):
        fields["generate_started_at"] = now_iso()
    if status in (PostStatus.COMPLETED, PostStatus.FAILED_GENERATION):
        fields["generate_completed_at"] = now_iso()

    db.update_post(post_id, **fields)
    return status


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
