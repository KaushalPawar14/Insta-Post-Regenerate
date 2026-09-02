"""
Cost calculation. Every figure here converts real, API-reported usage into a
USD amount -- never a flat per-post guess -- except the Apify fallback, which
is only used when Apify's own run doesn't report a real cost.

All prices verified against OpenAI's official pricing page
(developers.openai.com/api/docs/pricing, which platform.openai.com/docs/pricing
now redirects to) on 2026-09-02. Re-check this page if OpenAI changes pricing;
nothing here is fetched live.

gpt-5 (per 1M tokens):       input $1.25   |  cached input $0.125  |  output $10.00
gpt-image-2 (per 1M tokens): text input $5.00  |  image input $8.00  |  output $30.00

The cached-input discount is intentionally NOT applied even when OpenAI
reports cached tokens -- treating all input at the standard rate makes every
figure here a conservative (at-most) estimate, consistent with labeling every
cost in the UI as "Estimated cost."
"""

from typing import Any, Optional

from . import config

# --- gpt-5 (vision analysis) -----------------------------------------------
GPT5_INPUT_PER_1M_USD = 1.25
GPT5_OUTPUT_PER_1M_USD = 10.00

# --- gpt-image-2 (branded image generation) ---------------------------------
GPT_IMAGE_2_TEXT_INPUT_PER_1M_USD = 5.00
GPT_IMAGE_2_IMAGE_INPUT_PER_1M_USD = 8.00
GPT_IMAGE_2_IMAGE_OUTPUT_PER_1M_USD = 30.00


def vision_cost_usd(usage_metadata: Optional[dict]) -> float:
    """
    Cost of one analyze.py vision call, from the real token usage LangChain
    attaches to the response message (`AIMessage.usage_metadata`).

    `usage_metadata` is a plain dict at runtime (confirmed against the
    installed langchain-core: `UsageMetadata` subclasses `dict`, so this is
    `usage["input_tokens"]`, not `usage.input_tokens`) with keys
    `input_tokens` / `output_tokens` / `total_tokens`.
    """
    if not usage_metadata:
        return 0.0
    input_tokens = usage_metadata.get("input_tokens") or 0
    output_tokens = usage_metadata.get("output_tokens") or 0
    return (
        (input_tokens / 1_000_000) * GPT5_INPUT_PER_1M_USD
        + (output_tokens / 1_000_000) * GPT5_OUTPUT_PER_1M_USD
    )


def image_cost_usd(usage: Any) -> float:
    """
    Cost of one generate.py images.edit call, from the real token usage
    OpenAI's SDK attaches to the response (`ImagesResponse.usage`, a
    `Usage` object -- attribute access, confirmed against the installed
    openai package's response model).

    gpt-image-2 splits input into text tokens and image tokens (billed at
    different rates -- a reference image, like ours, is billed at the image
    rate) via `usage.input_tokens_details`; output is billed as image tokens
    via `usage.output_tokens`.
    """
    if usage is None:
        return 0.0
    details = getattr(usage, "input_tokens_details", None)
    text_tokens = getattr(details, "text_tokens", 0) or 0
    image_tokens = getattr(details, "image_tokens", 0) or 0
    output_tokens = getattr(usage, "output_tokens", 0) or 0
    return (
        (text_tokens / 1_000_000) * GPT_IMAGE_2_TEXT_INPUT_PER_1M_USD
        + (image_tokens / 1_000_000) * GPT_IMAGE_2_IMAGE_INPUT_PER_1M_USD
        + (output_tokens / 1_000_000) * GPT_IMAGE_2_IMAGE_OUTPUT_PER_1M_USD
    )


def apify_cost_usd(run_usage_total_usd: Optional[float], post_count: int) -> tuple[float, bool]:
    """
    Cost of one Apify actor run.

    Returns (total_usd, is_estimate). Prefers the run's own reported
    `usage_total_usd` (apify-client's `Run.usage_total_usd`, confirmed to
    exist on the installed SDK's model) when it's a real positive number.
    Falls back to `APIFY_ESTIMATED_COST_PER_POST_USD` x post_count and marks
    the result as an estimate, per the requirement that Apify cost be clearly
    labeled when it isn't a real, run-reported figure.
    """
    if run_usage_total_usd is not None and run_usage_total_usd > 0:
        return float(run_usage_total_usd), False
    return config.apify_estimated_cost_per_post_usd() * max(post_count, 1), True
