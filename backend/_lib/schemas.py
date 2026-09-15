"""
Pydantic shapes carried over from the original pipeline's `state.py`.

The original ran all three agents inside one in-process LangGraph
`StateGraph`, so a single `InstaWorkflowState` object was mutated as it flowed
Scraper -> Analyzer -> Generator.

Each stage now runs in its own stateless Vercel function invocation, so the
"state" lives in Postgres (`jobs` / `job_posts`) instead of in memory. The
field names below are kept identical to the original `PostData` so the ported
agent logic reads the same, and `from_row()` / `to_updates()` map them to and
from the database rows.

`AnalyzerOutput` is the structured-output schema the vision LLM is bound to.
Its field names and descriptions are unchanged from `agent_2_analyzer.py`,
because they are part of what the model is instructed to produce.
"""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# --- structured output schema for the vision LLM ---------------------------
# Carried over verbatim from agent_2_analyzer.py. The `description` strings are
# sent to the model as part of the JSON schema, so they are functional text,
# not comments -- do not reword them.
class AnalyzerOutput(BaseModel):
    image_generation_prompt: str = Field(
        description="A detailed description of the primary visual content, optimized for image generation models. Excludes text, logos, and layout."
    )
    extracted_text: str = Field(
        description="Exact transcription of all visible text in the image, excluding watermarks. Return an empty string if no text is present."
    )
    refined_caption: str = Field(
        description="A highly engaging, refined version of the original caption with relevant emojis."
    )


# --- post statuses ---------------------------------------------------------
class PostStatus:
    PENDING = "pending"
    # Multi-slide (carousel) posts only -- parked here right after scraping,
    # before any analysis, while the user reviews each slide's raw scraped
    # image and decides which to keep. Single-image posts never take this
    # value; they go PENDING -> ANALYZING exactly as before this stage
    # existed. See prepare_slides.py and the "Continue to analysis" route.
    AWAITING_SLIDE_SELECTION = "awaiting_slide_selection"
    ANALYZING = "analyzing"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    QUEUED_FOR_GENERATION = "queued_for_generation"
    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED_ANALYSIS = "failed_analysis"
    FAILED_GENERATION = "failed_generation"
    # User removed this post from awaiting_confirmation (or, for a carousel,
    # awaiting_slide_selection) before confirming. Terminal: never proceeds
    # to generation, whether via Confirm & Generate or otherwise. Excluded by
    # construction from Confirm & Generate's query (it only claims
    # AWAITING_CONFIRMATION rows), not by any extra filtering.
    REMOVED = "removed"


class SlideStatus:
    """Status values for one post_slides row -- meaningful only for a slide
    with include_in_analysis=True; an unchecked slide is stamped SKIPPED at
    Continue-time and never enters this state machine otherwise."""

    PENDING = "pending"
    ANALYZING = "analyzing"
    ANALYZED = "analyzed"
    FAILED_ANALYSIS = "failed_analysis"
    SKIPPED = "skipped"


class Brand:
    """The two image-generation brands. Matches job_post_brands.brand's CHECK
    constraint exactly -- keep both in sync if either ever changes."""

    FACTS4GENIUS = "facts4genius"
    FACTSBYTES = "factsbytes"


ALL_BRANDS = (Brand.FACTS4GENIUS, Brand.FACTSBYTES)


class BrandGenerationStatus:
    """Status values for one job_post_brands row -- a SUBSET of PostStatus
    below (only the generation-phase values are meaningful for a single
    brand's own generation attempt)."""

    QUEUED_FOR_GENERATION = "queued_for_generation"
    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED_GENERATION = "failed_generation"


class JobStatus:
    PENDING = "pending"
    SCRAPING = "scraping"
    ANALYZING = "analyzing"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    COMPLETED = "completed"
    FAILED = "failed"


# --- per-post record (the ported `PostData`) -------------------------------
class PostData(BaseModel):
    # Database identity (new -- replaces in-memory list position)
    row_id: Optional[str] = None
    job_id: Optional[str] = None
    user_id: Optional[str] = None

    # Agent 1: Scraped Data
    post_id: str
    popularity_score: int = 0
    comments: int = 0
    original_caption: str = ""
    raw_image_url: str = ""
    slide_count: int = 1

    # The shared, post-level caption -- promoted from the first checked
    # slide's own refined_caption once every checked slide has finished
    # analysis (see pipeline.refresh_post_analysis_status). Per-image Agent 2
    # output (prompt, transcribed text, thumbnail) lives on SlideData below,
    # not here, since a post can now have more than one slide -- see
    # migration_006_carousel_slides.sql.
    refined_caption: str = ""

    # Agent 3: Final Output. LEGACY: pre-multi-brand posts only.
    final_image_path: str = ""

    status: str = PostStatus.PENDING

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "PostData":
        """Build a PostData from a `job_posts` row."""
        return cls(
            row_id=row["id"],
            job_id=row.get("job_id"),
            user_id=row.get("user_id"),
            post_id=row.get("post_id") or "unknown_id",
            popularity_score=row.get("likes") or 0,
            comments=row.get("comments") or 0,
            original_caption=row.get("original_caption") or "",
            raw_image_url=row.get("raw_image_url") or "",
            slide_count=row.get("slide_count") or 1,
            refined_caption=row.get("refined_caption") or "",
            final_image_path=row.get("final_image_path") or "",
            status=row.get("status") or PostStatus.PENDING,
        )


# --- per-slide record --------------------------------------------------------
class SlideData(BaseModel):
    """
    One `post_slides` row: a single image within a post (the post's only
    slide for a plain image post, or one carousel entry). Carries the
    per-image Agent 2 output that used to live directly on `job_posts` before
    a post could have more than one image -- see
    migration_006_carousel_slides.sql.
    """

    row_id: Optional[str] = None
    post_id: Optional[str] = None
    job_id: Optional[str] = None
    user_id: Optional[str] = None

    slide_index: int = 0
    raw_image_url: str = ""
    # A Storage path in OUR bucket -- may already be populated by
    # prepare_slides.py (multi-slide posts) before analyze.py ever runs, or
    # populated by analyze.py itself here for the first time (single-image
    # posts, unchanged from before this feature existed).
    thumb_path: str = ""

    image_generation_prompt: str = ""
    extracted_text: str = ""
    refined_caption: str = ""

    status: str = SlideStatus.PENDING

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "SlideData":
        return cls(
            row_id=row["id"],
            post_id=row.get("post_id"),
            job_id=row.get("job_id"),
            user_id=row.get("user_id"),
            slide_index=row.get("slide_index") or 0,
            raw_image_url=row.get("raw_image_url") or "",
            thumb_path=row.get("thumb_path") or "",
            image_generation_prompt=row.get("image_generation_prompt") or "",
            extracted_text=row.get("extracted_text") or "",
            refined_caption=row.get("refined_caption") or "",
            status=row.get("status") or SlideStatus.PENDING,
        )

    def analyzer_updates(self) -> Dict[str, Any]:
        """Columns Agent 2 writes back to this slide's row."""
        return {
            "thumb_path": self.thumb_path,
            "image_generation_prompt": self.image_generation_prompt,
            "extracted_text": self.extracted_text,
            "refined_caption": self.refined_caption,
        }


# --- job record (the ported `InstaWorkflowState`) --------------------------
class InstaWorkflowState(BaseModel):
    job_id: Optional[str] = None
    user_id: Optional[str] = None
    instagram_url: str = Field(description="The input target channel URL")
    target_count: int = Field(description="The number of posts the user requested to scrape")
    input_type: str = "profile"  # "profile" | "post"
    target_posts: List[PostData] = Field(default_factory=list)
