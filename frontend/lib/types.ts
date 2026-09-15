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
  | "failed_generation"
  | "removed";

// --- multi-brand generation ---------------------------------------------
export type Brand = "facts4genius" | "factsbytes";
export const ALL_BRANDS: Brand[] = ["facts4genius", "factsbytes"];
export const BRAND_LABELS: Record<Brand, string> = {
  facts4genius: "Facts4Genius",
  factsbytes: "Facts Bytes",
};

/** Status values a single job_post_brands row can have -- a subset of PostStatus. */
export type BrandGenerationStatus =
  | "queued_for_generation"
  | "generating"
  | "completed"
  | "failed_generation";

export interface JobPostBrand {
  id: string;
  post_id: string;
  job_id: string;
  user_id: string;
  brand: Brand;
  status: BrandGenerationStatus;
  final_image_path: string | null;
  image_cost_usd: number;
  error: string | null;
  generate_started_at: string | null;
  generate_completed_at: string | null;
  created_at: string;
}

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
  /** Estimated cost of this job's Apify scrape, in USD. See PostCost. */
  apify_total_cost_usd: number;
  /** True unless Apify's run reported a real usage_total_usd figure. */
  apify_cost_is_estimate: boolean;
  /** Public /share/<token> link token. Null until the owner clicks Share. */
  share_token: string | null;
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
  /** Real cost of this post's vision (gpt-5) call, in USD. 0 until analysis completes. */
  vision_cost_usd: number;
  /** Real cost of this post's image generation (gpt-image-2) call, in USD. 0 until generation completes. */
  image_cost_usd: number;
  /** This post's apportioned share of the job's Apify scrape cost, in USD. */
  apify_cost_usd: number;
}

/** Must match MAX_POSTS_CEILING in backend/_lib/config.py and the
 * jobs.max_posts CHECK constraint in supabase/schema.sql -- there is no
 * shared config between the two services, so all three are kept in sync by
 * hand. */
export const MAX_POSTS_CEILING = 50;

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
  removed: "Removed",
};

/**
 * The normal, linear path a post moves through. `queued_for_generation` maps
 * onto the same step as `generating` -- from the user's perspective, pressing
 * Confirm All puts a post "in the generating phase," whether it's technically
 * queued or actively running.
 *
 * `failed_analysis`, `failed_generation`, and `removed` are terminal
 * off-ramps, not steps on this path -- the UI renders those as a distinct
 * badge state that interrupts the stepper at the point it branched off,
 * rather than forcing them into a linear sequence.
 */
export const PIPELINE_STEPS: { key: PostStatus; label: string }[] = [
  { key: "pending", label: "Scraped" },
  { key: "analyzing", label: "Analyzing" },
  { key: "awaiting_confirmation", label: "Awaiting confirmation" },
  { key: "generating", label: "Generating" },
  { key: "completed", label: "Completed" },
];

const STEP_INDEX_BY_STATUS: Partial<Record<PostStatus, number>> = {
  pending: 0,
  analyzing: 1,
  awaiting_confirmation: 2,
  queued_for_generation: 3,
  generating: 3,
  completed: 4,
};

export type TerminalOffRamp = "failed_analysis" | "failed_generation" | "removed";

const TERMINAL_STATUSES: TerminalOffRamp[] = ["failed_analysis", "failed_generation", "removed"];

export function terminalOffRamp(status: PostStatus): TerminalOffRamp | null {
  return (TERMINAL_STATUSES as PostStatus[]).includes(status) ? (status as TerminalOffRamp) : null;
}

/**
 * Step index (0-based) into PIPELINE_STEPS a post's status corresponds to,
 * or -- for a terminal off-ramp -- the step it branched off AT:
 *   failed_analysis    branched at step 1 (analyzing)      -- failed mid-analysis
 *   removed            branched at step 2 (awaiting_conf.) -- user removed it there
 *   failed_generation  branched at step 3 (generating)     -- confirmed, then failed
 */
export function stepIndexForStatus(status: PostStatus): number {
  const direct = STEP_INDEX_BY_STATUS[status];
  if (direct !== undefined) return direct;
  if (status === "failed_analysis") return 1;
  if (status === "removed") return 2;
  return 3; // failed_generation
}

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

/**
 * Same staleness check as `isStaleGeneration`, but scoped to ONE brand's own
 * job_post_brands row -- the precise check now that generation happens per
 * brand. `isStaleGeneration(post)` above still reflects the post-level
 * rollup (its generate_started_at is the EARLIEST brand's start), which is
 * too coarse to say "brand X specifically is stuck," so PostCard uses this
 * one for each brand slot instead.
 */
export function isStaleBrandGeneration(brand: JobPostBrand): boolean {
  if (brand.status !== "generating" || !brand.generate_started_at) return false;
  return Date.now() - new Date(brand.generate_started_at).getTime() > STALE_GENERATION_MS;
}

// --- cost -------------------------------------------------------------------
/**
 * Total cost of one post, in USD -- OpenAI (vision + image generation) only.
 *
 * Apify cost is deliberately excluded: the backend never computes or writes
 * job_posts.apify_cost_usd (it stays at its schema default of 0), because
 * the user runs on Apify's free credits and wants it treated as $0 / not
 * applicable, not shown even as a labeled estimate. See README "Cost
 * tracking". The column itself is left in place, unused, as the lowest-risk
 * way to reverse this later.
 *
 * Image-generation cost now comes from the post's job_post_brands rows
 * (pass whichever ones belong to this post) rather than a single
 * `post.image_cost_usd` column -- a post generated for both brands costs
 * more than one generated for a single brand, and this sums exactly the
 * brand rows that actually exist for it. `post.image_cost_usd` itself is
 * legacy (pre-multi-brand posts only; see migration_004's backfill) and is
 * no longer read here since those posts now have an equivalent
 * job_post_brands row instead.
 */
export function postCostUsd(post: JobPost, brands: JobPostBrand[] = []): number {
  const brandCost = brands.reduce((sum, b) => sum + (b.image_cost_usd || 0), 0);
  return (post.vision_cost_usd || 0) + brandCost;
}

/**
 * Fixed display-only conversion rate. Never a live currency API call, by
 * design -- see .env.example. 94.85 mirrors the backend's own default
 * (same sourcing, same date) so the two stay consistent if only one side's
 * env var gets set.
 */
export function usdToInrRate(): number {
  const raw = process.env.NEXT_PUBLIC_USD_TO_INR_RATE;
  const parsed = raw ? Number(raw) : NaN;
  return Number.isFinite(parsed) && parsed > 0 ? parsed : 94.85;
}

export function usdToInr(usd: number, rate: number = usdToInrRate()): number {
  return usd * rate;
}

export function formatInr(inr: number): string {
  if (inr === 0) return "₹0.00";
  if (inr < 0.01) return "<₹0.01";
  return `₹${inr.toLocaleString("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
}
