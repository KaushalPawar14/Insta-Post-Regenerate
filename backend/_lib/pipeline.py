"""Shared stage helpers: claiming work safely and keeping job status in sync."""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from . import db
from .schemas import BrandGenerationStatus, JobStatus, PostStatus, SlideStatus

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


def claim_slide_analysis(slide_id: str) -> Optional[Dict[str, Any]]:
    """
    Claim one slide for analysis. Mirrors the pre-carousel-feature
    `claim_analysis` (retargeted at post_slides instead of job_posts, since a
    post can now have more than one slide, each analyzed independently) --
    see migration_006_carousel_slides.sql.

    Recovers abandoned work: if a previous invocation claimed the slide and
    was killed mid-flight, a later delivery re-claims it. Analysis is cheap
    (one vision call), so re-running it is preferable to leaving a slide
    stuck forever.
    """
    row = db.claim_slide(
        slide_id,
        expect_status=SlideStatus.PENDING,
        set_status=SlideStatus.ANALYZING,
        analyze_started_at=now_iso(),
        error=None,
    )
    if row:
        return row

    current = db.get_slide(slide_id)
    if not current:
        return None

    retryable = {SlideStatus.ANALYZING, SlideStatus.FAILED_ANALYSIS}
    if current["status"] in retryable and is_stale(current.get("analyze_started_at")):
        return db.claim_slide(
            slide_id,
            expect_status=current["status"],
            set_status=SlideStatus.ANALYZING,
            analyze_started_at=now_iso(),
            error=None,
        )
    return None


def claim_brand_generation(slide_id: str, brand: str) -> Optional[Dict[str, Any]]:
    """
    Claim one (slide, brand) generation.

    Mirrors the pre-multi-brand `claim_generation`, retargeted first at
    `job_post_brands` keyed by post, and now at `job_post_brands` keyed by
    SLIDE (see migration_006_carousel_slides.sql -- a post can have more than
    one slide, each independently generated per brand). It only ever claims a
    row sitting in `queued_for_generation`, and recovers a row abandoned
    mid-generation by marking it `failed_generation` (so the user gets an
    explicit retry affordance for that ONE slide+brand, rather than the whole
    post) instead of leaving it stuck forever. Generation costs real money,
    so an automatic QStash retry must never silently re-trigger it -- claiming
    is what makes that impossible.

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
        slide_id,
        brand,
        expect_status=BrandGenerationStatus.QUEUED_FOR_GENERATION,
        set_status=BrandGenerationStatus.GENERATING,
        generate_started_at=now_iso(),
        error=None,
    )
    if row:
        return row

    current = db.get_post_brand(slide_id, brand)
    if not current:
        return None

    if current["status"] == BrandGenerationStatus.GENERATING and is_stale(
        current.get("generate_started_at")
    ):
        db.fail_post_brand(
            slide_id,
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


def compute_post_analysis_rollup_status(slide_rows: list) -> str:
    """
    Pure function: given all of a post's post_slides rows, return the
    job_posts.status rollup value for the analysis phase. Mirrors
    `compute_post_rollup_status` above exactly -- same shape, different
    terminal states -- so a post's status keeps meaning the same thing to
    every existing reader (PostStepper, stage-breakdown UI, ETA) whether it
    has one slide or several.

    Slides with status SKIPPED (unchecked in stage 2) are excluded entirely --
    they never run analysis and never influence this rollup.

        no relevant (non-skipped) slides at all   -> failed_analysis (defensive;
                                                      the Continue route requires
                                                      >=1 checked slide per post)
        any relevant slide analyzing               -> analyzing
        any relevant slide still pending            -> analyzing (not yet claimed)
        all relevant slides terminal, >=1 analyzed  -> awaiting_confirmation
        all relevant slides terminal, none analyzed -> failed_analysis
    """
    relevant = [s for s in slide_rows if s["status"] != SlideStatus.SKIPPED]
    if not relevant:
        return PostStatus.FAILED_ANALYSIS
    statuses = [s["status"] for s in relevant]
    if any(s == SlideStatus.ANALYZING for s in statuses):
        return PostStatus.ANALYZING
    if any(s == SlideStatus.PENDING for s in statuses):
        return PostStatus.ANALYZING
    # every relevant slide is terminal (analyzed or failed_analysis) here
    if any(s == SlideStatus.ANALYZED for s in statuses):
        return PostStatus.AWAITING_CONFIRMATION
    return PostStatus.FAILED_ANALYSIS


def refresh_post_analysis_status(post_id: str) -> str:
    """
    Roll this post's post_slides rows up into job_posts.status for the
    analysis phase (and, on first entering `analyzing`, stamp
    analyze_started_at; on reaching a terminal rollup state, stamp
    analyze_completed_at -- both preserved for the same reason
    `refresh_post_status` preserves the generation-phase timestamps).

    On reaching AWAITING_CONFIRMATION, also promotes the post's shared
    `refined_caption` from whichever CHECKED slide has the lowest
    slide_index among those that finished analyzing. This is race-safe
    precisely because it only ever runs inside the SAME call that computes
    the rollup as fully terminal -- there is exactly one such call (made by
    whichever slide happens to be the last to finish), and by that point
    every checked slide's own refined_caption is already final since
    include_in_analysis is immutable once a post leaves
    awaiting_slide_selection.
    """
    slide_rows = db.list_slides(post_id)
    status = compute_post_analysis_rollup_status(slide_rows)

    fields: Dict[str, Any] = {"status": status}
    post = db.get_post(post_id) or {}
    if status == PostStatus.ANALYZING and not post.get("analyze_started_at"):
        fields["analyze_started_at"] = now_iso()
    if status in (PostStatus.AWAITING_CONFIRMATION, PostStatus.FAILED_ANALYSIS):
        fields["analyze_completed_at"] = now_iso()
        if status == PostStatus.AWAITING_CONFIRMATION:
            analyzed = sorted(
                (s for s in slide_rows if s["status"] == SlideStatus.ANALYZED),
                key=lambda s: s["slide_index"],
            )
            if analyzed:
                fields["refined_caption"] = analyzed[0]["refined_caption"]

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
    # AWAITING_SLIDE_SELECTION is deliberately excluded from `in_flight` (no
    # automated work is happening -- it's blocked on the user, like
    # AWAITING_CONFIRMATION) and folded into the SAME job-level
    # "needs your attention" bucket below, per the decision to add no new
    # jobs.status value: the job-level status keeps its exact original enum
    # and meaning, and the finer detail of WHAT it's waiting on (slide
    # selection vs. brand confirmation) already lives at the post level,
    # same principle as every other job-level rollup in this file.
    awaiting_user = counts.get(PostStatus.AWAITING_CONFIRMATION, 0) + counts.get(
        PostStatus.AWAITING_SLIDE_SELECTION, 0
    )

    if settled == total:
        status = JobStatus.COMPLETED
    elif in_flight == 0 and awaiting_user:
        # Everything the pipeline can do on its own is done; the rest is
        # blocked on the user (confirming brands, or reviewing slides).
        status = JobStatus.AWAITING_CONFIRMATION
    else:
        status = JobStatus.ANALYZING

    db.update_job(job_id, status=status, updated_at=now_iso())
