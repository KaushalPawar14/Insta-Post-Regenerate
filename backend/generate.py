"""
Agent 3: branded image generation -- now runs ONCE PER SELECTED BRAND per
post, reusing the SAME Analyzer (Agent 2) output for every brand. The vision
LLM is never re-run for a second brand; only this stage (and its OpenAI
image-gen cost) multiplies per brand.

Ported from `nodes/agent_3_generator.py`. The OpenAI call itself is unchanged
apart from one addition carried over from before multi-brand: `quality` is
passed explicitly (default `medium`). Rationale -- Vercel's Hobby plan
enforces a HARD 300s function ceiling, and gpt-image-2 at 1024x1536 with
quality left at `auto` has been benchmarked at roughly 195s median / 280s
worst case. `medium` keeps generation comfortably inside the limit and makes
cost predictable. Override with IMAGE_QUALITY.

Each brand has its own protected prompt (`_lib/prompts.py`) and its own
reference template (`_lib/assets/`), selected by the `brand` field in this
function's payload:

  facts4genius -> render_generator_prompt          + reference_format.png
  factsbytes   -> render_factsbytes_prompt          + reference_format_factsbytes.png

Results are written to `job_post_brands` (one row per post+brand), NOT to
job_posts directly -- job_posts.status is a ROLLUP over those rows, computed
by `refresh_post_status` (see _lib/pipeline.py). This function claims and
updates exactly one (post, brand) row per invocation; it never touches the
other brand's row for the same post.

This function runs ONLY when a user has explicitly confirmed the post for
this brand (via Confirm & Generate, or the later "generate the other brand"
action) -- never automatically, and never for a brand nobody selected.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import base64  # noqa: E402
from io import BytesIO  # noqa: E402
from typing import Any, Dict  # noqa: E402

import requests  # noqa: E402
from openai import OpenAI  # noqa: E402
from PIL import Image  # noqa: E402

from _lib import config, db, pricing  # noqa: E402
from _lib.handler import TerminalError
from _lib.pipeline import claim_brand_generation, now_iso, refresh_job_status, refresh_post_status  # noqa: E402
from _lib.prompts import render_factsbytes_prompt, render_generator_prompt  # noqa: E402
from _lib.schemas import ALL_BRANDS, Brand, BrandGenerationStatus, PostData, PostStatus  # noqa: E402

_ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_lib", "assets")

REFERENCE_PATHS = {
    Brand.FACTS4GENIUS: os.path.join(_ASSETS_DIR, "reference_format.png"),
    Brand.FACTSBYTES: os.path.join(_ASSETS_DIR, "reference_format_factsbytes.png"),
}


def _render_prompt(brand: str, visual_prompt: str, text_transcription: str) -> str:
    if brand == Brand.FACTS4GENIUS:
        return render_generator_prompt(
            visual_prompt=visual_prompt,
            text_transcription=text_transcription,
        )
    return render_factsbytes_prompt(
        image_description=visual_prompt,
        text_as_is=text_transcription,
    )


def _maybe_drop_thumbnail(post: PostData, row_id: str, rollup_status: str) -> None:
    """
    The original scraped thumbnail was only ever needed to show the user what
    they were confirming. Once EVERY selected brand for this post has reached
    a terminal state (completed or failed_generation) -- not just the first
    one to finish, since the other may still be generating -- it's dropped.
    Safe to call after every brand completion: a second call for the same
    post is a harmless no-op (post.local_processed_image_path will already
    read empty once the first call clears it), and db.remove() is itself
    documented best-effort/idempotent.
    """
    if rollup_status in (PostStatus.COMPLETED, PostStatus.FAILED_GENERATION) and post.local_processed_image_path:
        db.remove([post.local_processed_image_path])
        db.update_post(row_id, thumb_path=None)


def run(payload: Dict[str, Any]) -> Dict[str, Any]:
    row_id = payload.get("post_row_id")
    brand = payload.get("brand")
    if not row_id:
        raise TerminalError("Missing post_row_id in payload.")
    if brand not in ALL_BRANDS:
        raise TerminalError(f"Missing or invalid brand in payload: {brand!r}.")

    brand_row = claim_brand_generation(row_id, brand)
    if brand_row is None:
        # Not queued for this brand (duplicate delivery, already generated,
        # already failed-and-not-yet-retried, or abandoned and now marked
        # failed). Never generate speculatively.
        return {"skipped": True}

    post_row = db.get_post(row_id)
    if not post_row:
        db.fail_post_brand(row_id, brand, f"Parent post {row_id} was not found.")
        refresh_post_status(row_id)
        raise TerminalError(f"Post {row_id} not found.")

    post = PostData.from_row(post_row)
    print(f"   -> Generating {brand} image for post (ID: {post.post_id})")

    reference_path = REFERENCE_PATHS[brand]
    if not os.path.exists(reference_path):
        db.fail_post_brand(row_id, brand, f"Reference image not found at '{reference_path}'.")
        status = refresh_post_status(row_id)
        refresh_job_status(post.job_id)
        _maybe_drop_thumbnail(post, row_id, status)
        raise TerminalError(f"Reference template missing from the bundle ({brand}).")

    visual_prompt = post.image_generation_prompt if post.image_generation_prompt else "No visual content provided."
    text_transcription = post.extracted_text if post.extracted_text else "No text present."

    formatted_prompt = _render_prompt(brand, visual_prompt, text_transcription)

    try:
        # --- SMART PRE-PROCESSING: Stretch to fit API naturally ------------
        original_ref = Image.open(reference_path).convert("RGB")

        # Stretch directly to 1024x1536 so there is ZERO black padding
        api_canvas = original_ref.resize((1024, 1536), Image.Resampling.LANCZOS)

        ref_buffer = BytesIO()
        api_canvas.save(ref_buffer, "PNG")
        ref_buffer.seek(0)
        # The OpenAI SDK infers the multipart filename from `.name`.
        ref_buffer.name = "reference_format.png"

        # --- API CALL ------------------------------------------------------
        client = OpenAI(api_key=config.openai_key())
        response = client.images.edit(
            model="gpt-image-2",
            image=ref_buffer,
            prompt=formatted_prompt,
            n=1,
            size="1024x1536",
            quality=config.image_quality(),
        )

        # Real per-call cost from OpenAI's own reported token usage
        # (ImagesResponse.usage -- attribute access, confirmed against the
        # installed openai SDK's response model), not a flat quality-tier
        # guess. See _lib/pricing.py for exactly how this is computed.
        image_cost = pricing.image_cost_usd(getattr(response, "usage", None))

        data_obj = response.data[0]

        b64_data = getattr(data_obj, "b64_json", None) or (
            data_obj.get("b64_json") if isinstance(data_obj, dict) else None
        )
        url_data = getattr(data_obj, "url", None) or (
            data_obj.get("url") if isinstance(data_obj, dict) else None
        )

        if b64_data:
            img_data = base64.b64decode(b64_data)
        elif url_data:
            img_data = requests.get(url_data, timeout=60).content
        else:
            raise ValueError("Could not extract image from the response.")

        # --- SMART POST-PROCESSING: Squeeze back to perfect Instagram ratio -
        generated_img = Image.open(BytesIO(img_data)).convert("RGB")

        # Since we didn't add padding, we don't crop! Just perfectly squeeze back to 1080x1350
        final_img = generated_img.resize((1080, 1350), Image.Resampling.LANCZOS)

        out_buffer = BytesIO()
        final_img.save(out_buffer, "PNG")

        # Brand suffix in the filename: a post can now have up to two stored
        # images, so they must not collide in the same Storage path.
        final_path = db.storage_path(post.user_id, post.job_id, f"{post.post_id}_{brand}_final.png")
        db.upload(final_path, out_buffer.getvalue(), "image/png")

    except Exception as exc:  # noqa: BLE001
        print(f"     Agent 3 ({brand}) generation failed for post {post.post_id}: {exc}")
        db.fail_post_brand(row_id, brand, str(exc))
        status = refresh_post_status(row_id)
        refresh_job_status(post.job_id)
        _maybe_drop_thumbnail(post, row_id, status)
        raise TerminalError(f"Image generation failed: {exc}") from exc

    db.update_post_brand(
        row_id,
        brand,
        final_image_path=final_path,
        status=BrandGenerationStatus.COMPLETED,
        generate_completed_at=now_iso(),
        image_cost_usd=image_cost,
        error=None,
    )
    print(f"     Success! Saved {brand} branded image to {final_path}")

    status = refresh_post_status(row_id)
    refresh_job_status(post.job_id)
    _maybe_drop_thumbnail(post, row_id, status)

    return {"post_id": post.post_id, "brand": brand, "final_image_path": final_path}
