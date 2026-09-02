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
