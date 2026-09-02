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
    ANALYZING = "analyzing"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    QUEUED_FOR_GENERATION = "queued_for_generation"
    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED_ANALYSIS = "failed_analysis"
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

    # Agent 2: Local Processing & AI Analysis Output
    # `local_processed_image_path` was a data_vault/ disk path in the original;
    # it is now a Supabase Storage object path for the transient thumbnail.
    local_processed_image_path: str = ""
    image_generation_prompt: str = ""  # Replaced generated_prompts
    extracted_text: str = ""           # Added for exactly transcribed text
    refined_caption: str = ""

    # Agent 3: Final Output
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
            local_processed_image_path=row.get("thumb_path") or "",
            image_generation_prompt=row.get("image_generation_prompt") or "",
            extracted_text=row.get("extracted_text") or "",
            refined_caption=row.get("refined_caption") or "",
            final_image_path=row.get("final_image_path") or "",
            status=row.get("status") or PostStatus.PENDING,
        )

    def analyzer_updates(self) -> Dict[str, Any]:
        """Columns Agent 2 writes back."""
        return {
            "thumb_path": self.local_processed_image_path,
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
