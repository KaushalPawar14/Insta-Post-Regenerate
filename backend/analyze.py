"""
Agent 2: vision analysis.

Ported from `nodes/agent_2_analyzer.py`. The LLM call is unchanged -- same
model (`gpt-5` via `langchain_openai`), same temperature, same structured
output schema, same multimodal message shape, and the same system prompt
loaded verbatim from `_lib/prompts.py`.

What changed, and only what the new execution model forced:
  - one post per invocation instead of a `for` loop over the whole state
  - the downloaded image is processed in memory instead of being written to
    `data_vault/2_original_images/`, then uploaded to Supabase Storage as a
    TRANSIENT thumbnail (deleted once the post reaches `completed`)
  - results go to `job_posts` columns instead of
    `data_vault/3_extracted_prompts/<id>.json`

*** This stage deliberately ends by parking the post in
`awaiting_confirmation` and publishing NOTHING. The Analyzer -> Generator edge
does not exist. Only an explicit user Confirm can start a paid generation. ***
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

from _lib import db  # noqa: E402
from _lib.handler import TerminalError
from _lib.pipeline import claim_analysis, now_iso, refresh_job_status  # noqa: E402
from _lib.prompts import VISION_PROMPT  # noqa: E402
from _lib.schemas import AnalyzerOutput, PostData, PostStatus  # noqa: E402

DOWNLOAD_TIMEOUT = 30


def run(payload: Dict[str, Any]) -> Dict[str, Any]:
    row_id = payload.get("post_row_id")
    if not row_id:
        raise TerminalError("Missing post_row_id in payload.")

    row = claim_analysis(row_id)
    if row is None:
        # Already analysed, already in flight, or deleted. Not an error.
        return {"skipped": True}

    post = PostData.from_row(row)
    print(f"  -> Processing post (ID: {post.post_id})")

    # --- STEP 1: Download & Convert Image (in memory, then Storage) --------
    if not post.raw_image_url:
        print(f"     No image URL found for {post.post_id}")
        db.fail_post(row_id, PostStatus.FAILED_ANALYSIS, "No image URL on the scraped post.")
        refresh_job_status(post.job_id)
        raise TerminalError("No image URL.")

    try:
        response = requests.get(post.raw_image_url, timeout=DOWNLOAD_TIMEOUT)
    except requests.RequestException as exc:
        db.fail_post(row_id, PostStatus.FAILED_ANALYSIS, f"Image download failed: {exc}")
        refresh_job_status(post.job_id)
        raise TerminalError(f"Image download failed: {exc}") from exc

    if response.status_code != 200:
        print(f"     Failed to download image for {post.post_id}")
        db.fail_post(
            row_id,
            PostStatus.FAILED_ANALYSIS,
            f"Image download returned HTTP {response.status_code}. Instagram CDN "
            "URLs expire quickly -- re-running the job usually fixes this.",
        )
        refresh_job_status(post.job_id)
        raise TerminalError(f"Image download HTTP {response.status_code}.")

    # Identical processing to the original, just without touching disk.
    img = Image.open(BytesIO(response.content))

    # Convert to RGB (removes alpha channel if PNG/WEBP, fixes HEIC issues conceptually)
    rgb_im = img.convert("RGB")

    # Resize to 768px to save API costs while keeping bold text readable
    rgb_im.thumbnail((768, 768))

    buffer = BytesIO()
    rgb_im.save(buffer, "JPEG", quality=90)
    jpeg_bytes = buffer.getvalue()

    thumb_path = db.storage_path(post.user_id, post.job_id, f"thumb_{post.post_id}.jpg")
    db.upload(thumb_path, jpeg_bytes, "image/jpeg")
    post.local_processed_image_path = thumb_path

    # --- STEP 2: Vision LLM Analysis --------------------------------------
    print("     Analyzing image and rewriting caption...")
    base64_image = base64.b64encode(jpeg_bytes).decode("utf-8")

    llm = ChatOpenAI(model="gpt-5", temperature=0.7)
    structured_llm = llm.with_structured_output(AnalyzerOutput)

    # Build the multimodal message
    message = HumanMessage(
        content=[
            {"type": "text", "text": f"Original Caption: {post.original_caption}\n\nAnalyze this image and caption."},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}},
        ]
    )

    try:
        result = structured_llm.invoke([SystemMessage(content=VISION_PROMPT), message])
    except Exception as exc:  # noqa: BLE001
        print(f"     LLM Analysis failed for {post.post_id}: {exc}")
        db.update_post(row_id, thumb_path=thumb_path)
        db.fail_post(row_id, PostStatus.FAILED_ANALYSIS, f"Vision analysis failed: {exc}")
        refresh_job_status(post.job_id)
        raise TerminalError(f"Vision analysis failed: {exc}") from exc

    post.image_generation_prompt = result.image_generation_prompt
    post.extracted_text = result.extracted_text
    post.refined_caption = result.refined_caption
    print("     Success: Generated prompts and new caption.")

    # --- STEP 3: Persist, then STOP for user confirmation ------------------
    db.update_post(
        row_id,
        **post.analyzer_updates(),
        status=PostStatus.AWAITING_CONFIRMATION,
        analyze_completed_at=now_iso(),
        error=None,
    )
    refresh_job_status(post.job_id)

    # No queue.publish() here, by design.
    return {"post_id": post.post_id, "status": PostStatus.AWAITING_CONFIRMATION}


