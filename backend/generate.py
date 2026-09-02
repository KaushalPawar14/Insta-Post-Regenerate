"""
Agent 3: branded image generation.

Ported from `nodes/agent_3_generator.py`. The OpenAI call is unchanged apart
from one addition: `quality` is now passed explicitly (default `medium`).
Rationale -- Vercel's Hobby plan enforces a HARD 300s function ceiling, and
gpt-image-2 at 1024x1536 with quality left at `auto` has been benchmarked at
roughly 195s median / 280s worst case. `medium` keeps generation comfortably
inside the limit and makes cost predictable. Override with IMAGE_QUALITY.

The prompt itself comes verbatim from `_lib/prompts.py`, including the single
authorised edit pinning the black gradient's start to the vertical midpoint of
the "INSTAGRAM | FACTS4GENIUS" brand text line.

The same reference template is used, byte-for-byte, from
`_lib/assets/reference_format.png`. The pre-processing (stretch to 1024x1536)
and post-processing (squeeze back to 1080x1350) are unchanged; the only
difference is that both happen in memory instead of via
`data_vault/temp_reference.png`.

This function runs ONLY when a user has explicitly confirmed the post.
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

from _lib import config, db  # noqa: E402
from _lib.handler import TerminalError
from _lib.pipeline import claim_generation, now_iso, refresh_job_status  # noqa: E402
from _lib.prompts import render_generator_prompt  # noqa: E402
from _lib.schemas import PostData, PostStatus  # noqa: E402

REFERENCE_IMAGE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "_lib", "assets", "reference_format.png"
)


def run(payload: Dict[str, Any]) -> Dict[str, Any]:
    row_id = payload.get("post_row_id")
    if not row_id:
        raise TerminalError("Missing post_row_id in payload.")

    row = claim_generation(row_id)
    if row is None:
        # Not queued for generation (duplicate delivery, already generated, or
        # abandoned and now marked failed). Never generate speculatively.
        return {"skipped": True}

    post = PostData.from_row(row)
    print(f"   -> Generating final image for post (ID: {post.post_id})")

    if not os.path.exists(REFERENCE_IMAGE_PATH):
        db.fail_post(
            row_id,
            PostStatus.FAILED_GENERATION,
            f"Reference image not found at '{REFERENCE_IMAGE_PATH}'.",
        )
        refresh_job_status(post.job_id)
        raise TerminalError("Reference template missing from the bundle.")

    visual_prompt = post.image_generation_prompt if post.image_generation_prompt else "No visual content provided."
    text_transcription = post.extracted_text if post.extracted_text else "No text present."

    formatted_prompt = render_generator_prompt(
        visual_prompt=visual_prompt,
        text_transcription=text_transcription,
    )

    try:
        # --- SMART PRE-PROCESSING: Stretch to fit API naturally ------------
        original_ref = Image.open(REFERENCE_IMAGE_PATH).convert("RGB")

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

        final_path = db.storage_path(post.user_id, post.job_id, f"{post.post_id}_final.png")
        db.upload(final_path, out_buffer.getvalue(), "image/png")

    except Exception as exc:  # noqa: BLE001
        print(f"     Agent 3 generation failed for post {post.post_id}: {exc}")
        db.fail_post(row_id, PostStatus.FAILED_GENERATION, str(exc))
        refresh_job_status(post.job_id)
        raise TerminalError(f"Image generation failed: {exc}") from exc

    db.update_post(
        row_id,
        final_image_path=final_path,
        status=PostStatus.COMPLETED,
        generate_completed_at=now_iso(),
        error=None,
    )
    print(f"     Success! Saved perfect ratio branded image to {final_path}")

    # The original scraped thumbnail was only ever needed to show the user
    # what they were confirming. Now that the generated image exists, drop it
    # -- originals are never retained long-term.
    if post.local_processed_image_path:
        db.remove([post.local_processed_image_path])
        db.update_post(row_id, thumb_path=None)

    refresh_job_status(post.job_id)
    return {"post_id": post.post_id, "final_image_path": final_path}


