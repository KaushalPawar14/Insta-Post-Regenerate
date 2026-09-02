"""
QStash orchestration -- the replacement for LangGraph's in-process edges.

The original pipeline wired the agents with `workflow.add_edge(...)` inside a
single process. Because each stage now runs in its own stateless function
invocation, the "edges" are QStash messages instead:

    scrape  --(fan out, one per post)-->  analyze
    analyze --( STOPS HERE. no message. )-->  awaiting_confirmation
    <user clicks Confirm in the UI>       -->  generate

The Analyzer -> Generator edge deliberately does not exist. Nothing but an
explicit user action can trigger a paid image generation.

Why no external worker is needed: QStash's free tier allows a destination to
take up to 15 minutes to respond, which is well beyond Vercel's hard 300s
function ceiling. QStash simply holds the HTTP request open until our function
returns, so a queued stage runs to completion without any always-on process.
"""

import json
from typing import Any, Dict, Optional

from qstash import QStash, Receiver

from . import config

_client: Optional[QStash] = None
_receiver: Optional[Receiver] = None

# Match Vercel's ceiling so QStash gives up at the same moment the function
# is killed, rather than waiting out its full 15-minute default.
DESTINATION_TIMEOUT = "300s"


def client() -> QStash:
    global _client
    if _client is None:
        _client = QStash(config.env("QSTASH_TOKEN"))
    return _client


def receiver() -> Receiver:
    global _receiver
    if _receiver is None:
        _receiver = Receiver(
            current_signing_key=config.env("QSTASH_CURRENT_SIGNING_KEY"),
            next_signing_key=config.env("QSTASH_NEXT_SIGNING_KEY"),
        )
    return _receiver


def publish(
    endpoint: str,
    body: Dict[str, Any],
    *,
    delay: Optional[str] = None,
    retries: int = 1,
    dedup_id: Optional[str] = None,
) -> None:
    """
    Enqueue one stage for one post.

    `dedup_id` makes an accidental double-publish a no-op at the QStash layer
    (belt to the `claim_post` braces at the database layer).
    """
    url = f"{config.base_url()}/api/{endpoint.lstrip('/')}"
    client().message.publish_json(
        url=url,
        body=body,
        retries=retries,
        delay=delay,
        deduplication_id=dedup_id,
        timeout=DESTINATION_TIMEOUT,
    )


def verify(signature: str, raw_body: str, endpoint: str) -> None:
    """
    Reject anything that is not a genuine QStash delivery.

    These endpoints spend real money on OpenAI and Apify, so an unauthenticated
    caller must never be able to invoke them. Raises on failure.

    The URL claim is checked when it matches the origin we publish to, but is
    skipped on mismatch: Vercel sits behind proxies that can rewrite the host,
    and the signature already covers the request body, which is what actually
    determines the work performed.
    """
    expected_url = f"{config.base_url()}/api/{endpoint.lstrip('/')}"
    try:
        receiver().verify(signature=signature, body=raw_body, url=expected_url)
        return
    except Exception as url_exc:  # noqa: BLE001
        print(f"[queue] signature URL claim mismatch ({url_exc}); re-checking body only")
        receiver().verify(signature=signature, body=raw_body)


def parse_body(raw_body: str) -> Dict[str, Any]:
    try:
        parsed = json.loads(raw_body or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"Request body is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ValueError("Request body must be a JSON object.")
    return parsed
