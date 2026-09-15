"""
Supabase access layer -- the replacement for the original pipeline's
`data_vault/` folder writes.

Mapping from the original disk layout to storage here:

    data_vault/1_scraped_json/latest_scrape.json  ->  `jobs` + `job_posts` rows
    data_vault/2_original_images/<id>.jpg         ->  Storage, TRANSIENT
                                                      (deleted once the post
                                                      reaches `completed`)
    data_vault/3_extracted_prompts/<id>.json      ->  `job_posts` columns
    data_vault/4_final_generated_posts/<id>.png   ->  Storage, kept until the
                                                      user deletes the job

These functions run with the service-role key, which BYPASSES Row Level
Security. Every write therefore sets `user_id` explicitly -- never infer it,
and never expose this key to the browser.
"""

from typing import Any, Dict, List, Optional

from supabase import Client, create_client

from . import config

_client: Optional[Client] = None


def sb() -> Client:
    """Lazily-built service-role client, reused across warm invocations."""
    global _client
    if _client is None:
        _client = create_client(config.supabase_url(), config.supabase_service_key())
    return _client


# --- jobs ------------------------------------------------------------------
def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    res = sb().table("jobs").select("*").eq("id", job_id).limit(1).execute()
    return res.data[0] if res.data else None


def update_job(job_id: str, **fields: Any) -> None:
    if fields:
        sb().table("jobs").update(fields).eq("id", job_id).execute()


def fail_job(job_id: str, message: str) -> None:
    update_job(job_id, status="failed", error=_truncate(message))


def claim_job(job_id: str, *, expect_status: str, set_status: str, **fields: Any) -> Optional[Dict[str, Any]]:
    """
    Atomically move a job from `expect_status` to `set_status`. Same
    compare-and-swap pattern as `claim_post` below -- a single
    `UPDATE ... WHERE id = ? AND status = ?` is atomic in Postgres.

    This is what guarantees a paid, non-idempotent external call (starting an
    Apify actor run, or fanning out one analyze message per scraped post) can
    be reached by AT MOST ONE invocation of a stage function per job, no
    matter how many times QStash redelivers its message -- a retry after a
    slow/failed response, a genuine duplicate delivery, or two poll deliveries
    racing each other. Call this to claim ownership of the transition BEFORE
    making the external call, not after -- claiming after the fact only
    guards against a second invocation also finishing successfully, not
    against a second invocation also starting the external side effect.

    Returns the updated row, or None if the job was not in `expect_status`
    (already claimed by another invocation, or already moved further/failed).
    """
    payload = {"status": set_status, **fields}
    res = (
        sb()
        .table("jobs")
        .update(payload)
        .eq("id", job_id)
        .eq("status", expect_status)
        .execute()
    )
    return res.data[0] if res.data else None


# --- job_posts -------------------------------------------------------------
def get_post(row_id: str) -> Optional[Dict[str, Any]]:
    res = sb().table("job_posts").select("*").eq("id", row_id).limit(1).execute()
    return res.data[0] if res.data else None


def update_post(row_id: str, **fields: Any) -> None:
    if fields:
        sb().table("job_posts").update(fields).eq("id", row_id).execute()


def fail_post(row_id: str, status: str, message: str) -> None:
    update_post(row_id, status=status, error=_truncate(message))


def insert_posts(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not rows:
        return []
    res = sb().table("job_posts").insert(rows).execute()
    return res.data or []


def claim_post(row_id: str, *, expect_status: str, set_status: str, **fields: Any) -> Optional[Dict[str, Any]]:
    """
    Atomically move a post from `expect_status` to `set_status`.

    This is the idempotency guard that makes QStash's at-least-once delivery
    safe. A single `UPDATE ... WHERE id = ? AND status = ?` is atomic in
    Postgres, so if QStash re-delivers a message (retry after a timeout, or a
    duplicate), the second attempt matches zero rows and returns None -- so we
    never pay OpenAI twice for the same post.

    Returns the updated row, or None if the post was not in `expect_status`.
    """
    payload = {"status": set_status, **fields}
    res = (
        sb()
        .table("job_posts")
        .update(payload)
        .eq("id", row_id)
        .eq("status", expect_status)
        .execute()
    )
    return res.data[0] if res.data else None


def count_posts_by_status(job_id: str) -> Dict[str, int]:
    res = sb().table("job_posts").select("status").eq("job_id", job_id).execute()
    counts: Dict[str, int] = {}
    for row in res.data or []:
        counts[row["status"]] = counts.get(row["status"], 0) + 1
    return counts


# --- post_slides -------------------------------------------------------------
# One row per slide (a plain image post has exactly one). See
# migration_006_carousel_slides.sql for the full rationale.
def get_slide(slide_id: str) -> Optional[Dict[str, Any]]:
    res = sb().table("post_slides").select("*").eq("id", slide_id).limit(1).execute()
    return res.data[0] if res.data else None


def list_slides(post_id: str) -> List[Dict[str, Any]]:
    res = (
        sb()
        .table("post_slides")
        .select("*")
        .eq("post_id", post_id)
        .order("slide_index", desc=False)
        .execute()
    )
    return res.data or []


def insert_slides(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not rows:
        return []
    res = sb().table("post_slides").insert(rows).execute()
    return res.data or []


def update_slide(slide_id: str, **fields: Any) -> None:
    if fields:
        sb().table("post_slides").update(fields).eq("id", slide_id).execute()


def fail_slide(slide_id: str, message: str) -> None:
    update_slide(slide_id, status="failed_analysis", error=_truncate(message))


def claim_slide(slide_id: str, *, expect_status: str, set_status: str, **fields: Any) -> Optional[Dict[str, Any]]:
    """
    Atomically move a slide from `expect_status` to `set_status`. Same
    compare-and-swap pattern as `claim_post` -- guarantees the paid vision
    call for one slide can be reached by AT MOST ONE invocation, no matter
    how many times QStash redelivers the message for that slide.
    """
    payload = {"status": set_status, **fields}
    res = (
        sb()
        .table("post_slides")
        .update(payload)
        .eq("id", slide_id)
        .eq("status", expect_status)
        .execute()
    )
    return res.data[0] if res.data else None


# --- job_post_brands ---------------------------------------------------------
# One row per (slide, brand) generation. `post_id` stays denormalized on
# every row alongside the real key, `slide_id` -- see
# migration_006_carousel_slides.sql for why: every post-level rollup query
# below (list_post_brands) keeps working unmodified across a post with more
# than one slide.
def get_post_brand(slide_id: str, brand: str) -> Optional[Dict[str, Any]]:
    res = (
        sb()
        .table("job_post_brands")
        .select("*")
        .eq("slide_id", slide_id)
        .eq("brand", brand)
        .limit(1)
        .execute()
    )
    return res.data[0] if res.data else None


def list_post_brands(post_id: str) -> List[Dict[str, Any]]:
    """Every brand row across EVERY slide of this post -- what the post-level
    generation rollup (compute_post_rollup_status) is computed over."""
    res = sb().table("job_post_brands").select("*").eq("post_id", post_id).execute()
    return res.data or []


def list_post_brands_for_slide(slide_id: str) -> List[Dict[str, Any]]:
    """Just one slide's own brand rows -- used to decide whether THAT slide's
    thumbnail can be dropped yet, independently of its siblings."""
    res = sb().table("job_post_brands").select("*").eq("slide_id", slide_id).execute()
    return res.data or []


def insert_post_brand(row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    res = sb().table("job_post_brands").insert(row).execute()
    return res.data[0] if res.data else None


def update_post_brand(slide_id: str, brand: str, **fields: Any) -> None:
    if fields:
        sb().table("job_post_brands").update(fields).eq("slide_id", slide_id).eq("brand", brand).execute()


def fail_post_brand(slide_id: str, brand: str, message: str) -> None:
    update_post_brand(slide_id, brand, status="failed_generation", error=_truncate(message))


def claim_post_brand(
    slide_id: str, brand: str, *, expect_status: str, set_status: str, **fields: Any
) -> Optional[Dict[str, Any]]:
    """
    Atomically move a (slide, brand) generation from `expect_status` to
    `set_status`. Same compare-and-swap pattern as `claim_post` -- a single
    `UPDATE ... WHERE slide_id = ? AND brand = ? AND status = ?` is atomic in
    Postgres, so this is what guarantees the paid images.edit call for one
    slide+brand can be reached by AT MOST ONE invocation, no matter how many
    times QStash redelivers the message for that pair.

    Returns the updated row, or None if the row was not in `expect_status`.
    """
    payload = {"status": set_status, **fields}
    res = (
        sb()
        .table("job_post_brands")
        .update(payload)
        .eq("slide_id", slide_id)
        .eq("brand", brand)
        .eq("status", expect_status)
        .execute()
    )
    return res.data[0] if res.data else None


# --- storage ---------------------------------------------------------------
def storage_path(user_id: str, job_id: str, filename: str) -> str:
    """
    Object paths are `<user_id>/<job_id>/<filename>`.

    The leading segment is the owner's id, which is what the Storage RLS
    policies match against -- so one visitor can never read another's images
    even with a guessed path.
    """
    return f"{user_id}/{job_id}/{filename}"


def upload(path: str, data: bytes, content_type: str) -> str:
    sb().storage.from_(config.bucket()).upload(
        path,
        data,
        {"content-type": content_type, "upsert": "true"},
    )
    return path


def download(path: str) -> bytes:
    """
    Read an object back from OUR OWN bucket -- used by analyze.py to reuse a
    slide's already-downloaded, already-resized thumbnail (created by
    prepare_slides.py) instead of re-fetching from Instagram's CDN a second
    time, which may have expired by then anyway. Never used for anything
    outside this app's own private bucket.
    """
    return sb().storage.from_(config.bucket()).download(path)


def remove(paths: List[str]) -> None:
    """Best-effort delete. Never let cleanup failure break the pipeline."""
    paths = [p for p in paths if p]
    if not paths:
        return
    try:
        sb().storage.from_(config.bucket()).remove(paths)
    except Exception as exc:  # noqa: BLE001 -- cleanup must not be fatal
        print(f"[db] non-fatal: failed to remove {paths}: {exc}")


def _truncate(message: str, limit: int = 800) -> str:
    message = str(message)
    return message if len(message) <= limit else message[: limit - 3] + "..."
