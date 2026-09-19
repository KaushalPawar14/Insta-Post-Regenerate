"""
Agent 3: branded image generation -- runs ONCE PER SELECTED BRAND PER SLIDE,
reusing that SLIDE's own Analyzer (Agent 2) output. The vision LLM is never
re-run for a second brand, and a post's other slides are never touched by
one slide's generation call.

Ported from `nodes/agent_3_generator.py`. The OpenAI call itself is unchanged
apart from one addition carried over from before multi-brand: `quality` is
passed explicitly (default `medium`). Rationale -- Vercel's Hobby plan
enforces a HARD 300s function ceiling, and gpt-image-2 at the legacy
1024x1536 size with quality left at `auto` was benchmarked at roughly 195s
median / 280s worst case. `medium` keeps generation comfortably inside the
limit and makes cost predictable. Override with IMAGE_QUALITY. (The active
1088x1360 size, see USE_LEGACY_1024x1536_STRETCH below, has ~6% fewer total
pixels than 1024x1536 and is expected to generate at least as fast, but was
not separately re-benchmarked -- worth confirming against real generation
times once live.)

Each brand has its own protected prompt (`_lib/prompts.py`) and its own
reference template (`_lib/assets/`), selected by the `brand` field in this
function's payload:

  facts4genius -> render_generator_prompt          + reference_format.png
  factsbytes   -> render_factsbytes_prompt          + reference_format_factsbytes.png

Results are written to `job_post_brands` (one row per slide+brand), NOT to
job_posts or post_slides directly -- job_posts.status is a ROLLUP over ALL of
a post's slides' job_post_brands rows (see `refresh_post_status` in
_lib/pipeline.py), unaffected by a post having more than one slide because
`post_id` stays denormalized on every job_post_brands row. This function
claims and updates exactly one (slide, brand) row per invocation; it never
touches any other slide or brand for the same post.

This function runs ONLY when a user has explicitly confirmed a slide for
this brand (via Confirm & Generate, or the later "generate the other brand"
action) -- never automatically, and never for a brand nobody selected, and
never for a slide the user left unchecked in stage 2.
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
from _lib.pipeline import (  # noqa: E402
    claim_brand_generation,
    compute_post_rollup_status,
    now_iso,
    refresh_job_status,
    refresh_post_status,
)
from _lib.prompts import render_factsbytes_prompt, render_generator_prompt  # noqa: E402
from _lib.schemas import ALL_BRANDS, Brand, BrandGenerationStatus, SlideData  # noqa: E402

_ASSETS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_lib", "assets")

REFERENCE_PATHS = {
    Brand.FACTS4GENIUS: os.path.join(_ASSETS_DIR, "reference_format.png"),
    Brand.FACTSBYTES: os.path.join(_ASSETS_DIR, "reference_format_factsbytes.png"),
}

# --- IMAGE SIZE CONFIGURATION -----------------------------------------------
# 2026-09-19: switched from native 1024x1536 (2:3) + a non-uniform stretch to
# native 1088x1360 -- an EXACT 4:5 match for Instagram's 1080x1350 delivery
# ratio (1360/1088 == 1350/1080 == 1.25). Both dimensions are multiples of 16
# and the ratio is within gpt-image-2's documented 1:3-3:1 range (confirmed
# against OpenAI's own API reference, not a third party). This removes the
# stretch distortion with zero cropping and zero padding -- verified against
# 8 real generations across both brands (varied source photos, caption
# lengths 25-326 chars). Real measured cost: ~$0.063/image vs. the old
# ~$0.058/image (~9% higher, confirmed via this app's own cost tracking on
# real API responses -- not an assumption).
#
# TO REVERT to the previous (pre-2026-09-19) behavior: change ONLY the line
# below to True. Every affected call in this file (the reference pre-stretch,
# the images.edit size parameter, and the final resize's effective behavior)
# branches on this single flag -- nothing else needs to change.
USE_LEGACY_1024x1536_STRETCH = False

if USE_LEGACY_1024x1536_STRETCH:
    # --- PREVIOUS CONFIGURATION (pre-2026-09-19), kept working as an -------
    # --- instant rollback path, not dead code ------------------------------
    # Native 1024x1536 (2:3) does not match the 1080x1350 (4:5) delivery
    # ratio, so the final resize below performs a non-uniform stretch
    # (visible as mild horizontal distortion). This was the app's original,
    # long-running behavior before the aspect-ratio investigation.
    _API_SIZE = "1024x1536"
    _API_CANVAS = (1024, 1536)
else:
    # --- CURRENT CONFIGURATION (active) ------------------------------------
    # Native 1088x1360 already matches the delivery ratio exactly, so the
    # final resize below is a plain proportional downscale: zero crop, zero
    # stretch, zero padding.
    _API_SIZE = "1088x1360"
    _API_CANVAS = (1088, 1360)


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


def _maybe_drop_slide_thumbnail(slide: SlideData, slide_id: str) -> None:
    """
    The original scraped/durable thumbnail for THIS SLIDE was only ever
    needed to show the user what they were confirming. Once EVERY selected
    brand for THIS SLIDE (not necessarily every slide of the post -- a
    sibling slide may still be generating) has reached a terminal state
    (completed or failed_generation), it's dropped. Computed from just this
    slide's own job_post_brands rows (`list_post_brands_for_slide`), reusing
    the same pure rollup function `refresh_post_status` uses at the whole-
    post granularity -- it works identically at either granularity since it
    only ever looks at the `status` field of whatever rows it's given.

    Safe to call after every brand completion: a second call for the same
    slide is a harmless no-op (slide.thumb_path will already read empty once
    the first call clears it), and db.remove() is itself documented
    best-effort/idempotent.
    """
    slide_brand_rows = db.list_post_brands_for_slide(slide_id)
    slide_rollup = compute_post_rollup_status(slide_brand_rows)
    if slide_rollup in ("completed", "failed_generation") and slide.thumb_path:
        db.remove([slide.thumb_path])
        db.update_slide(slide_id, thumb_path=None)


def run(payload: Dict[str, Any]) -> Dict[str, Any]:
    slide_id = payload.get("slide_row_id")
    brand = payload.get("brand")
    if not slide_id:
        raise TerminalError("Missing slide_row_id in payload.")
    if brand not in ALL_BRANDS:
        raise TerminalError(f"Missing or invalid brand in payload: {brand!r}.")

    brand_row = claim_brand_generation(slide_id, brand)
    if brand_row is None:
        # Not queued for this brand (duplicate delivery, already generated,
        # already failed-and-not-yet-retried, or abandoned and now marked
        # failed). Never generate speculatively.
        return {"skipped": True}

    slide_row = db.get_slide(slide_id)
    if not slide_row:
        db.fail_post_brand(slide_id, brand, f"Parent slide {slide_id} was not found.")
        raise TerminalError(f"Slide {slide_id} not found.")

    slide = SlideData.from_row(slide_row)
    post_row = db.get_post(slide.post_id) if slide.post_id else None
    if not post_row:
        db.fail_post_brand(slide_id, brand, f"Parent post {slide.post_id} was not found.")
        status = refresh_post_status(slide.post_id) if slide.post_id else None
        if slide.job_id:
            refresh_job_status(slide.job_id)
        raise TerminalError(f"Post {slide.post_id} not found.")

    print(f"   -> Generating {brand} image for slide {slide.slide_index} of post (ID: {post_row.get('post_id')})")

    reference_path = REFERENCE_PATHS[brand]
    if not os.path.exists(reference_path):
        db.fail_post_brand(slide_id, brand, f"Reference image not found at '{reference_path}'.")
        refresh_post_status(slide.post_id)
        refresh_job_status(slide.job_id)
        _maybe_drop_slide_thumbnail(slide, slide_id)
        raise TerminalError(f"Reference template missing from the bundle ({brand}).")

    visual_prompt = slide.image_generation_prompt if slide.image_generation_prompt else "No visual content provided."
    text_transcription = slide.extracted_text if slide.extracted_text else "No text present."

    formatted_prompt = _render_prompt(brand, visual_prompt, text_transcription)

    try:
        # --- SMART PRE-PROCESSING: Stretch to fit API naturally ------------
        original_ref = Image.open(reference_path).convert("RGB")

        # Stretch directly to the API canvas size so there is ZERO black
        # padding. Size comes from USE_LEGACY_1024x1536_STRETCH above.
        api_canvas = original_ref.resize(_API_CANVAS, Image.Resampling.LANCZOS)

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
            size=_API_SIZE,
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

        # --- SMART POST-PROCESSING: perfect Instagram ratio -----------------
        generated_img = Image.open(BytesIO(img_data)).convert("RGB")

        # This single resize is shared by both configurations above. With
        # the active 1088x1360 canvas the ratio already matches 1080x1350
        # exactly (1360/1088 == 1350/1080), so this is a plain proportional
        # downscale -- zero distortion. With USE_LEGACY_1024x1536_STRETCH =
        # True, the source ratio (2:3) does not match the target (4:5), so
        # this same line performs the old non-uniform stretch instead -- no
        # separate code path is needed for that case.
        final_img = generated_img.resize((1080, 1350), Image.Resampling.LANCZOS)

        out_buffer = BytesIO()
        final_img.save(out_buffer, "PNG")

        # Slide index AND brand in the filename: a post can now have several
        # slides, each with up to two stored images, so none of them may
        # collide in the same Storage path.
        final_path = db.storage_path(
            slide.user_id,
            slide.job_id,
            f"{post_row.get('post_id')}_slide{slide.slide_index}_{brand}_final.png",
        )
        db.upload(final_path, out_buffer.getvalue(), "image/png")

    except Exception as exc:  # noqa: BLE001
        print(f"     Agent 3 ({brand}) generation failed for slide {slide.slide_index}: {exc}")
        db.fail_post_brand(slide_id, brand, str(exc))
        refresh_post_status(slide.post_id)
        refresh_job_status(slide.job_id)
        _maybe_drop_slide_thumbnail(slide, slide_id)
        raise TerminalError(f"Image generation failed: {exc}") from exc

    db.update_post_brand(
        slide_id,
        brand,
        final_image_path=final_path,
        status=BrandGenerationStatus.COMPLETED,
        generate_completed_at=now_iso(),
        image_cost_usd=image_cost,
        error=None,
    )
    print(f"     Success! Saved {brand} branded image to {final_path}")

    refresh_post_status(slide.post_id)
    refresh_job_status(slide.job_id)
    _maybe_drop_slide_thumbnail(slide, slide_id)

    return {"post_id": slide.post_id, "slide_id": slide_id, "brand": brand, "final_image_path": final_path}
