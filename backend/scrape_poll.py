"""
Agent 1 (part 2 of 2): poll the Apify run, then fan out one analyze message
per post.

This is where the original `agent_1_scraper.py` field extraction and
sort-by-likes live. The extraction is deliberately tolerant, because
single-post mode has been confirmed against Apify's documentation but not yet
against real actor output -- see `normalise_item()`.

IDEMPOTENCY: reading the run's status and its dataset are safe to repeat --
they're pure reads, Apify doesn't bill for re-reading them, and re-running
them costs nothing beyond a little compute. What is NOT safe to repeat is the
insert-posts-and-fan-out-to-analyze step below: two overlapping deliveries of
this function (a natural next-attempt poll racing a redelivered earlier one,
for instance) could otherwise both observe the run as SUCCEEDED and both
insert duplicate `job_posts` rows and publish duplicate (billable) analyze
messages. `db.claim_job`'s SCRAPING -> ANALYZING compare-and-swap, taken
immediately before that step, guarantees it runs at most once per job.
"""

import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from typing import Any, Dict, List, Optional  # noqa: E402

from apify_client import ApifyClient  # noqa: E402

from _lib import config, db, queue  # noqa: E402
from _lib.handler import TerminalError
from _lib.pipeline import now_iso  # noqa: E402
from _lib.schemas import JobStatus, PostStatus, SlideStatus  # noqa: E402

# ~15s x 40 = up to 10 minutes of scraping before we give up. Each poll is a
# separate short invocation, so none of this counts against the 300s ceiling.
MAX_ATTEMPTS = 40
POLL_DELAY = "15s"

# Every status apify-client's Run model declares (checked against the
# installed SDK's type hints, not assumed): READY, RUNNING, SUCCEEDED,
# FAILED, TIMING-OUT, TIMED-OUT, ABORTING, ABORTED. ABORTING is a transient
# in-flight state (a stop was requested but the run hasn't settled yet) --
# treated as in-progress so we keep polling until it reaches ABORTED, rather
# than falling through to "unexpected status" below.
TERMINAL_FAILURE_STATES = {"FAILED", "ABORTED", "TIMED-OUT", "TIMING-OUT"}
IN_PROGRESS_STATES = {"READY", "RUNNING", "ABORTING"}

# Apify/Instagram's known "can't return full post data" fallback shape --
# confirmed against a real sample before writing any of this (see
# normalise_item's docstring). Any OTHER value in an item's `error` field is
# unrecognised: still attempted via the same fallback extraction (a fallback
# `image` + `description` is the best available signal either way), but
# logged rather than silently treated as identical to this one.
RESTRICTED_ERROR_VALUE = "restricted_page"

# Matches the shortcode out of a permalink like
# "https://www.instagram.com/p/DdT2IZYCM62/" (also "/reel/" and "/tv/").
_SHORTCODE_RE = re.compile(r"/(?:p|reel|tv)/([A-Za-z0-9_-]+)")

# Matches Instagram's own auto-generated restricted-page description shape:
# `<username> on <date>: "<caption>".` (confirmed against a real sample --
# see normalise_item's docstring). DOTALL so the caption's own newlines
# don't stop the match; the capture group is greedy so it extends to the
# LAST quote in the string even if the caption itself contains one.
_RESTRICTED_CAPTION_RE = re.compile(r'^.*?:\s*"(.*)"\.?\s*$', re.DOTALL)


def _first(item: Dict[str, Any], *keys: str) -> Optional[Any]:
    """Return the first key present with a truthy value."""
    for key in keys:
        value = item.get(key)
        if value:
            return value
    return None


def _shortcode_from_url(url: Optional[str]) -> Optional[str]:
    if not url:
        return None
    match = _SHORTCODE_RE.search(url)
    return match.group(1) if match else None


def _extract_restricted_caption(description: str) -> str:
    """
    Pull just the quoted caption text out of Instagram's auto-generated
    restricted-page description, e.g. from
    `k4knowledge on September 15, 2026: "✨ The Secret Meaning..."` extract
    `✨ The Secret Meaning...`. Falls back to the raw description if the
    pattern doesn't match cleanly, rather than failing -- some restricted
    responses may not follow this exact shape.
    """
    if not description:
        return ""
    match = _RESTRICTED_CAPTION_RE.match(description)
    return match.group(1) if match else description


def _slide_urls(item: Dict[str, Any], thumbnail: str) -> List[str]:
    """
    Extract the ordered list of slide image URLs for one post.

    Confirmed against two real sample Apify actor outputs (one `type:
    "Image"`, one `type: "Sidecar"` with 5 slides) before writing this:
    a Sidecar post's top-level `images` array holds the slide URLs in order,
    and matches its `childPosts[i].displayUrl` 1:1 by index. A plain Image
    post has an empty `images` array and exactly one implicit slide --
    `thumbnail` (already resolved by the caller). This holds for both
    profile-scraped and single-post-URL fetches: `scrape.py` sends the SAME
    actor the SAME `directUrls` + `resultsType: "posts"` shape for both
    modes (differing only in `resultsLimit`), so a post's own item shape is a
    function of the post's type, not of which mode fetched it.
    """
    if item.get("type") == "Sidecar":
        images = item.get("images")
        if isinstance(images, list) and images:
            urls = [str(u) for u in images if u]
            if urls:
                return urls
    return [thumbnail]


def normalise_item(item: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Map one Apify dataset item onto the fields the original pipeline used.

    The canonical field names (`id`, `likesCount`, `commentsCount`, `caption`,
    `displayUrl`) are tried first, exactly as in `agent_1_scraper.py`.
    Fallbacks exist because we have not yet validated single-post-mode output
    against a live run; if the actor names a field differently there, this
    degrades instead of producing an empty post.

    RESTRICTED FALLBACK: for some posts, Instagram/Apify can't return the
    normal shape at all and instead returns a restricted-page fallback --
    recognisable by an `error` field and the absence of every normal field
    (`type`, `images`, `likesCount`, `id`, ...). Confirmed against a real
    sample before writing this:

        {"error": "restricted_page", "errorDescription": "...",
         "image": "<one fallback photo url>",
         "description": "<username> on <date>: \\"<caption>\\".",
         "media_id": "<numeric id>", "shared_entity_id": "<same numeric id>",
         "url": "https://www.instagram.com/p/<shortcode>/", ...}

    Only `image` and `description` carry anything usable -- there is no
    `likesCount`/`commentsCount` at all (both default to 0 via the same
    fallback logic below that already handles them being merely absent on a
    normal item, not a new code path) and no reliable multi-image signal, so
    this always becomes a single-slide post via the same `_slide_urls` call
    every other item goes through (it only treats an item as a carousel when
    `type == "Sidecar"`, which a restricted item never has). This is handled
    entirely HERE, in the one place both profile-scrape and single-post-URL
    jobs already share for normalising a raw Apify item -- see `_slide_urls`'s
    docstring for why that sharing holds.

    Returns None for items that are not usable posts at all (no image by any
    means) -- the actor can emit profile/detail objects alongside posts, and
    a post with no image is useless to the Analyzer.
    """
    restricted_caption: Optional[str] = None
    error = item.get("error")
    if error:
        if error != RESTRICTED_ERROR_VALUE:
            print(
                f"[scrape_poll] unrecognised Apify 'error' field {error!r} on an item "
                "(expected 'restricted_page') -- attempting the same fallback "
                "image/description extraction anyway, since that's the best "
                "signal available either way."
            )
        thumbnail = item.get("image")
        restricted_caption = _extract_restricted_caption(item.get("description") or "")
    else:
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

    # `media_id`/`shared_entity_id` and the permalink's own shortcode are
    # extra fallbacks that only ever matter for a restricted item -- every
    # normal item already has `id` (confirmed against real samples), so this
    # extension is a no-op for the non-restricted path.
    post_id = (
        _first(item, "id", "shortCode", "shortcode", "postId", "media_id", "shared_entity_id")
        or _shortcode_from_url(item.get("url"))
        or "unknown_id"
    )

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

    caption = restricted_caption if restricted_caption is not None else (item.get("caption") or "")

    return {
        "id": str(post_id),
        "likes": likes,
        "comments": comments,
        "caption": caption,
        "thumbnail_url": str(thumbnail),
        "slide_urls": _slide_urls(item, str(thumbnail)),
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

    # apify-client >=3.x returns a typed `Run` object here (or None if the
    # run cannot be found), not a dict -- attribute access, not dict-style
    # .get(key). Confirmed against the installed apify-client 3.1.3's Run
    # pydantic model. This was the second half of the production bug: even
    # once scrape.py correctly saved a run_id, polling it would have crashed
    # identically the first time this line executed.
    actor_run = client.run(run_id).get()
    if actor_run is None:
        db.fail_job(job_id, f"Apify run {run_id} could not be found (it may have been deleted).")
        raise TerminalError("Apify run not found.")
    state = (actor_run.status or "").upper()

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
    dataset_id = actor_run.default_dataset_id
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

    # Everything above this point is a pure read (Apify run status, dataset
    # items) or pure computation -- safe to repeat as many times as QStash
    # cares to redeliver this message. Everything below is not: inserting
    # job_posts rows and fanning out analyze messages must happen at most
    # once per job. Two overlapping deliveries of this function (a natural
    # next-attempt poll racing a redelivered earlier one, for instance) could
    # otherwise both reach this point -- both having observed the SAME
    # SUCCEEDED run, since nothing above mutates any state -- and both insert
    # duplicate posts and publish duplicate (billable) analyze messages.
    #
    # Atomically claim the SCRAPING -> ANALYZING transition now, immediately
    # before the first side effect. Only one delivery can win it; every other
    # delivery (including ones that already did all the same reads above)
    # returns here without inserting anything or publishing anything.
    claimed = db.claim_job(
        job_id,
        expect_status=JobStatus.SCRAPING,
        set_status=JobStatus.ANALYZING,
        scrape_completed_at=now_iso(),
        updated_at=now_iso(),
    )
    if claimed is None:
        return {"skipped": True, "reason": "already transitioned to analyzing"}

    input_type = job.get("input_type") or "profile"
    if input_type == "post":
        # One post: nothing to rank.
        selected = raw_scraped_data[:1]
    else:
        # scrape.py always fetches the full server-side max pool for a
        # profile job (see its module docstring, point 3) -- `raw_scraped_data`
        # is that larger pool, not the user's requested count. This is where
        # the ranking actually happens: sort descending by likes across that
        # whole pool, then take only the user's requested top N. Everything
        # NOT selected here is discarded right now -- it was never inserted
        # as a job_posts row and is not reachable by anything below this
        # point, so it can never be analyzed or generated (the two steps
        # that cost OpenAI money).
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
            "slide_count": len(item["slide_urls"]),
            "status": PostStatus.PENDING,
        }
        for index, item in enumerate(selected)
    ]
    inserted = db.insert_posts(rows)
    db.update_job(job_id, total_posts=len(inserted))

    # Apify cost is deliberately NOT computed or stored here. The user runs
    # on Apify's free credits and wants $0 / not-applicable, not even a
    # labeled estimate -- so job_posts.apify_cost_usd and
    # jobs.apify_total_cost_usd/apify_cost_is_estimate are simply never
    # written and stay at their schema defaults (0 / true), which the
    # frontend's cost total no longer reads. pricing.apify_cost_usd() and
    # config.apify_estimated_cost_per_post_usd() are left in place, unused,
    # rather than deleted -- the lowest-risk way to reverse this later if the
    # free-credits situation changes. See README "Cost tracking".

    # Create each post's slide row(s), then fan out ONE message per post:
    #
    #   - a plain image post (exactly one slide) goes straight to `analyze`,
    #     targeting that one slide -- unchanged in spirit from before
    #     carousels existed, just addressed by slide id instead of post id.
    #   - a carousel goes to the NEW `prepare_slides` stage instead, which
    #     downloads every slide's image into our own Storage BEFORE the user
    #     is ever shown stage 2 (so an arbitrarily long human review never
    #     depends on Instagram's CDN link staying alive -- see
    #     prepare_slides.py's module docstring). No analysis is fanned out
    #     yet for a carousel; that happens only once the user hits
    #     "Continue to analysis" for the slides they kept checked.
    #
    # Matched by `rank` rather than by position in `inserted` -- the bulk
    # insert's response order is not a contract worth relying on.
    inserted_by_rank = {row["rank"]: row for row in inserted}
    for index, item in enumerate(selected):
        row = inserted_by_rank.get(index)
        if not row:
            continue  # this one row's insert didn't come back; nothing to fan out
        slide_urls = item["slide_urls"]
        slide_rows = db.insert_slides(
            [
                {
                    "post_id": row["id"],
                    "job_id": job_id,
                    "user_id": job["user_id"],
                    "slide_index": slide_index,
                    "raw_image_url": url,
                    "status": SlideStatus.PENDING,
                }
                for slide_index, url in enumerate(slide_urls)
            ]
        )
        if len(slide_urls) <= 1:
            if slide_rows:
                queue.publish(
                    "analyze",
                    {"slide_row_id": slide_rows[0]["id"]},
                    retries=2,
                    dedup_id=f"analyze-{slide_rows[0]['id']}",
                )
        else:
            queue.publish(
                "prepare_slides",
                {"post_row_id": row["id"]},
                retries=2,
                dedup_id=f"prepare-slides-{row['id']}",
            )

    print(f"Agent 1: Successfully secured top {len(inserted)} posts and updated state.")
    return {"posts": len(inserted)}


