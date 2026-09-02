export type JobStatus =
  | "pending"
  | "scraping"
  | "analyzing"
  | "awaiting_confirmation"
  | "completed"
  | "failed";

export type PostStatus =
  | "pending"
  | "analyzing"
  | "awaiting_confirmation"
  | "queued_for_generation"
  | "generating"
  | "completed"
  | "failed_analysis"
  | "failed_generation";

export interface Job {
  id: string;
  user_id: string;
  input_type: "profile" | "post";
  input_url: string;
  max_posts: number;
  status: JobStatus;
  apify_run_id: string | null;
  total_posts: number;
  error: string | null;
  created_at: string;
  updated_at: string;
  scrape_started_at: string | null;
  scrape_completed_at: string | null;
}

export interface JobPost {
  id: string;
  job_id: string;
  user_id: string;
  post_id: string;
  likes: number;
  comments: number;
  original_caption: string;
  raw_image_url: string;
  rank: number;
  thumb_path: string | null;
  image_generation_prompt: string;
  extracted_text: string;
  refined_caption: string;
  final_image_path: string | null;
  downloaded: boolean;
  status: PostStatus;
  error: string | null;
  created_at: string;
  analyze_started_at: string | null;
  analyze_completed_at: string | null;
  generate_started_at: string | null;
  generate_completed_at: string | null;
}

export const MAX_POSTS_CEILING = 100;

/** Statuses where the pipeline is doing work on its own. */
export const IN_FLIGHT: PostStatus[] = [
  "pending",
  "analyzing",
  "queued_for_generation",
  "generating",
];

export const STAGE_LABELS: Record<PostStatus, string> = {
  pending: "Queued",
  analyzing: "Analyzing",
  awaiting_confirmation: "Ready for your review",
  queued_for_generation: "Queued for generation",
  generating: "Generating image",
  completed: "Completed",
  failed_analysis: "Analysis failed",
  failed_generation: "Generation failed",
};

/**
 * A generation that has been running longer than Vercel's hard 300s function
 * ceiling (plus slack) can never complete -- the function was killed. The UI
 * uses this to offer a Retry instead of spinning forever.
 */
export const STALE_GENERATION_MS = 330_000;

export function isStaleGeneration(post: JobPost): boolean {
  if (post.status !== "generating" || !post.generate_started_at) return false;
  return Date.now() - new Date(post.generate_started_at).getTime() > STALE_GENERATION_MS;
}
