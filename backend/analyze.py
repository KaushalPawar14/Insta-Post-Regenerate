"""
Agent 2: vision analysis -- runs ONCE PER CHECKED SLIDE, not once per post.

Ported from `nodes/agent_2_analyzer.py`. The LLM call itself is unchanged --
same model (`gpt-5` via `langchain_openai`), same temperature, same
structured output schema, same multimodal message shape, and the same
system prompt loaded verbatim from `_lib/prompts.py`.

What changed for carousel support: this function now operates on ONE
`post_slides` row (payload: `slide_row_id`) instead of one `job_posts` row.
A plain image post has exactly one slide and behaves identically to before
this feature existed -- it just reaches this function via that slide's row
instead of the post's row directly. A carousel's checked slides each get
their own independent invocation, exactly the same fan-out pattern already
used for per-brand generation.

Image source depends on whether prepare_slides.py already ran for this
slide:
  - Multi-slide posts: prepare_slides.py already downloaded this slide's
    image into OUR bucket (`post_slides.thumb_path`) before the user ever
    saw stage 2. Reused here via db.download() instead of re-fetching from
    Instagram a second time (which may have expired by now anyway).
  - Plain image posts: unchanged from before this feature -- fetched live
    from `raw_image_url`, resized, and uploaded here for the first time.

job_posts.status is a ROLLUP over this post's post_slides rows (see
`refresh_post_analysis_status` in _lib/pipeline.py) for the SAME reason
job_posts.status is a rollup over job_post_brands during generation -- it
keeps its exact existing enum and meaning, so nothing downstream needed to
change to accommodate a post having more than one slide.

*** This stage deliberately ends by parking the post in
`awaiting_confirmation` (once every checked slide is done) and publishing
NOTHING. The Analyzer -> Generator edge does not exist. Only an explicit
user Confirm can start a paid generation. ***
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import base64  # noqa: E402
from io import BytesIO  # noqa: E402
from typing import Any, Dict  # noqa: E402

import requests  # noqa: E402
from langchain_core.messages import HumanMessage, SystemMessage  # noqa: E402
from langchain_openai import ChatOpenAI  # noqa: E402
from PIL import Image  # noqa: E402

from _lib import db, pricing  # noqa: E402
from _lib.handler import TerminalError
from _lib.pipeline import (  # noqa: E402
    claim_slide_analysis,
    now_iso,
    refresh_job_status,
    refresh_post_analysis_status,
)
from _lib.prompts import VISION_PROMPT  # noqa: E402
from _lib.schemas import AnalyzerOutput, SlideData, SlideStatus  # noqa: E402

DOWNLOAD_TIMEOUT = 30


def _fail(slide_id: str, post_id: str, job_id: str, message: str) -> None:
    db.fail_slide(slide_id, message)
    refresh_post_analysis_status(post_id)
    refresh_job_status(job_id)


def run(payload: Dict[str, Any]) -> Dict[str, Any]:
    slide_id = payload.get("slide_row_id")
    if not slide_id:
        raise TerminalError("Missing slide_row_id in payload.")

    row = claim_slide_analysis(slide_id)
    if row is None:
        # Already analysed, already in flight, unchecked (skipped), or
        # deleted. Not an error.
        return {"skipped": True}

    slide = SlideData.from_row(row)
    post_row = db.get_post(slide.post_id) if slide.post_id else None
    if not post_row:
        _fail(slide_id, slide.post_id or "", slide.job_id or "", f"Parent post {slide.post_id} was not found.")
        raise TerminalError(f"Post {slide.post_id} not found.")

    # Promote the post to ANALYZING promptly -- mirrors the pre-carousel
    # behavior where claiming the post's own row for analysis set
    # job_posts.status directly, in the same atomic step.
    refresh_post_analysis_status(slide.post_id)

    original_caption = post_row.get("original_caption") or ""
    print(f"  -> Processing slide {slide.slide_index} of post (ID: {post_row.get('post_id')})")

    # --- STEP 1: Get this slide's image bytes -------------------------------
    if slide.thumb_path:
        # Multi-slide post: prepare_slides.py already fetched and resized
        # this slide's image into our own Storage. Reuse those exact bytes
        # rather than hitting Instagram's CDN a second time.
        try:
            jpeg_bytes = db.download(slide.thumb_path)
        except Exception as exc:  # noqa: BLE001
            _fail(slide_id, slide.post_id, slide.job_id, f"Could not read this slide's stored image: {exc}")
            raise TerminalError(f"Could not read stored image: {exc}") from exc
        thumb_path = slide.thumb_path
    else:
        # Plain image post: unchanged from before this feature -- fetch the
        # raw CDN url live, resize, and upload here for the first time.
        if not slide.raw_image_url:
            _fail(slide_id, slide.post_id, slide.job_id, "No image URL on the scraped post.")
            raise TerminalError("No image URL.")

        try:
            response = requests.get(slide.raw_image_url, timeout=DOWNLOAD_TIMEOUT)
        except requests.RequestException as exc:
            _fail(slide_id, slide.post_id, slide.job_id, f"Image download failed: {exc}")
            raise TerminalError(f"Image download failed: {exc}") from exc

        if response.status_code != 200:
            print(f"     Failed to download image for slide {slide.slide_index}")
            _fail(
                slide_id,
                slide.post_id,
                slide.job_id,
                f"Image download returned HTTP {response.status_code}. Instagram CDN "
                "URLs expire quickly -- re-running the job usually fixes this.",
            )
            raise TerminalError(f"Image download HTTP {response.status_code}.")

        img = Image.open(BytesIO(response.content))
        rgb_im = img.convert("RGB")
        rgb_im.thumbnail((768, 768))

        buffer = BytesIO()
        rgb_im.save(buffer, "JPEG", quality=90)
        jpeg_bytes = buffer.getvalue()

        thumb_path = db.storage_path(slide.user_id, slide.job_id, f"thumb_{post_row.get('post_id')}.jpg")
        db.upload(thumb_path, jpeg_bytes, "image/jpeg")
    slide.thumb_path = thumb_path

    # --- STEP 2: Vision LLM Analysis --------------------------------------
    print("     Analyzing image and rewriting caption...")
    base64_image = base64.b64encode(jpeg_bytes).decode("utf-8")

    llm = ChatOpenAI(model="gpt-5", temperature=0.7)
    # include_raw=True so the real token usage LangChain attaches to the raw
    # response is available for cost tracking. Without it, .invoke() returns
    # only the parsed Pydantic object with no usage information at all.
    structured_llm = llm.with_structured_output(AnalyzerOutput, include_raw=True)

    message = HumanMessage(
        content=[
            {"type": "text", "text": f"Original Caption: {original_caption}\n\nAnalyze this image and caption."},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}},
        ]
    )

    try:
        response = structured_llm.invoke([SystemMessage(content=VISION_PROMPT), message])
    except Exception as exc:  # noqa: BLE001
        print(f"     LLM Analysis failed for slide {slide.slide_index}: {exc}")
        db.update_slide(slide_id, thumb_path=thumb_path)
        _fail(slide_id, slide.post_id, slide.job_id, f"Vision analysis failed: {exc}")
        raise TerminalError(f"Vision analysis failed: {exc}") from exc

    # With include_raw=True, a parsing failure is returned here rather than
    # raised -- same failure, different shape, so it needs its own check to
    # preserve the original behavior of failing the slide either way.
    result = response.get("parsed")
    if result is None:
        parsing_error = response.get("parsing_error")
        print(f"     LLM Analysis failed for slide {slide.slide_index}: {parsing_error}")
        db.update_slide(slide_id, thumb_path=thumb_path)
        _fail(slide_id, slide.post_id, slide.job_id, f"Vision analysis failed: {parsing_error}")
        raise TerminalError(f"Vision analysis failed: {parsing_error}")

    slide.image_generation_prompt = result.image_generation_prompt
    slide.extracted_text = result.extracted_text
    slide.refined_caption = result.refined_caption
    print("     Success: Generated prompts and new caption.")

    # `usage_metadata` is a dict at runtime (confirmed against the installed
    # langchain-core -- `UsageMetadata` subclasses `dict`), so this is
    # usage["input_tokens"], not attribute access. See _lib/pricing.py.
    raw_message = response.get("raw")
    usage_metadata = getattr(raw_message, "usage_metadata", None) if raw_message else None
    vision_cost = pricing.vision_cost_usd(usage_metadata)

    # --- STEP 3: Persist this slide, then roll the post up ------------------
    db.update_slide(
        slide_id,
        **slide.analyzer_updates(),
        status=SlideStatus.ANALYZED,
        analyze_completed_at=now_iso(),
        vision_cost_usd=vision_cost,
        error=None,
    )
    # No queue.publish() here, by design -- refresh_post_analysis_status only
    # ever moves the post to awaiting_confirmation once EVERY checked slide
    # has reached a terminal state, and does so without publishing anything.
    refresh_post_analysis_status(slide.post_id)
    refresh_job_status(slide.job_id)

    return {"slide_id": slide_id, "post_id": slide.post_id, "status": SlideStatus.ANALYZED}
