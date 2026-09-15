"""
NEW stage, inserted between scraping and analysis for carousel/Sidecar posts
only. Plain image posts never reach this file -- they fan straight from
scrape_poll.py to analyze.py, exactly as before this feature existed.

Purpose: download every slide's image into OUR OWN Storage bucket, right
after scraping and before the user is ever shown stage 2 (slide selection).

Why eagerly, rather than leaving each slide as a live Instagram CDN link
until it's actually needed: stage 2 introduces a genuinely new, human-gated
pause between scraping and analysis that didn't exist before this feature.
Instagram's CDN URLs "expire quickly" (see README "Known constraints") --
tolerable today because analysis fires immediately after scraping with no
wait, but a carousel could now sit waiting on a reviewer for any length of
time. Two things need a durable copy that will never depend on that CDN
link surviving the wait:

  1. Stage 2's own preview -- the review has to show SOMETHING no matter how
     long the user takes.
  2. A slide the user leaves UNCHECKED never gets analyzed or generated, so
     its "final" appearance in the result is its ORIGINAL scraped image --
     and this app has no TTL on jobs at all ("nothing is deleted
     automatically"), so that original has to remain viewable indefinitely,
     not just for the length of one review session.

analyze.py reuses this same durable copy (via db.download()) instead of
re-fetching from Instagram a second time, for a slide that's checked.

IDEMPOTENCY: the PENDING -> AWAITING_SLIDE_SELECTION claim below prevents a
redelivery from re-running this whole stage from scratch, but a redelivery
that lands DURING a genuinely still-in-progress or previously-interrupted
attempt is still useful -- it fills in whichever slides are still missing
their durable copy (checked via thumb_path being unset) rather than
re-downloading ones that already succeeded. Nothing here is a billable
external call, so re-attempting a slide costs bandwidth, not money.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from io import BytesIO  # noqa: E402
from typing import Any, Dict  # noqa: E402

import requests  # noqa: E402
from PIL import Image  # noqa: E402

from _lib import db  # noqa: E402
from _lib.handler import TerminalError
from _lib.schemas import PostStatus  # noqa: E402

DOWNLOAD_TIMEOUT = 30


def _download_and_store_slide(slide: Dict[str, Any]) -> str:
    """Fetch one slide's raw image and store a resized copy in our bucket.
    Same processing as analyze.py's single-image thumbnail (RGB, 768px,
    JPEG q90) -- consistent sizing whether a slide is later fed to the
    vision model here or downloaded fresh by analyze.py for a plain post."""
    response = requests.get(slide["raw_image_url"], timeout=DOWNLOAD_TIMEOUT)
    if response.status_code != 200:
        raise ValueError(f"Image download returned HTTP {response.status_code}.")

    img = Image.open(BytesIO(response.content))
    rgb_im = img.convert("RGB")
    rgb_im.thumbnail((768, 768))

    buffer = BytesIO()
    rgb_im.save(buffer, "JPEG", quality=90)

    path = db.storage_path(
        slide["user_id"], slide["job_id"], f"slide_{slide['post_id']}_{slide['slide_index']}.jpg"
    )
    db.upload(path, buffer.getvalue(), "image/jpeg")
    return path


def run(payload: Dict[str, Any]) -> Dict[str, Any]:
    post_id = payload.get("post_row_id")
    if not post_id:
        raise TerminalError("Missing post_row_id in payload.")

    claimed = db.claim_post(
        post_id,
        expect_status=PostStatus.PENDING,
        set_status=PostStatus.AWAITING_SLIDE_SELECTION,
    )
    if claimed is None:
        current = db.get_post(post_id)
        if not current:
            return {"skipped": True}
        if current["status"] != PostStatus.AWAITING_SLIDE_SELECTION:
            # Already progressed past this stage (or failed/removed/deleted
            # in the meantime) -- nothing to do.
            return {"skipped": True, "post_status": current.get("status")}
        # Already claimed by an earlier attempt at this same message -- fall
        # through and fill in any slides an interrupted attempt left without
        # a durable thumbnail, rather than treating this as a no-op.

    slides = db.list_slides(post_id)
    prepared = 0
    failed = 0

    for slide in slides:
        if slide.get("thumb_path"):
            continue  # already durably stored by an earlier attempt
        try:
            path = _download_and_store_slide(slide)
            db.update_slide(slide["id"], thumb_path=path, error=None)
            prepared += 1
        except Exception as exc:  # noqa: BLE001
            print(f"     Could not prepare slide {slide['slide_index']} for post {post_id}: {exc}")
            db.fail_slide(slide["id"], f"Could not fetch this slide's image: {exc}")
            failed += 1

    return {"post_id": post_id, "prepared": prepared, "failed": failed}
