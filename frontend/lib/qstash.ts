import { Client } from "@upstash/qstash";

let client: Client | null = null;

function qstash(): Client {
  if (client) return client;
  const token = process.env.QSTASH_TOKEN;
  if (!token) throw new Error("Missing QSTASH_TOKEN. See SETUP.md.");
  client = new Client({ token });
  return client;
}

/** Public origin QStash calls back to. Must be the real deployed HTTPS URL. */
export function baseUrl(): string {
  const explicit = process.env.PUBLIC_BASE_URL?.trim();
  if (explicit) return explicit.replace(/\/+$/, "");

  const host =
    process.env.VERCEL_PROJECT_PRODUCTION_URL?.trim() || process.env.VERCEL_URL?.trim();
  if (host) return host.startsWith("http") ? host.replace(/\/+$/, "") : `https://${host}`;

  throw new Error(
    "Cannot determine the app's public URL. Set PUBLIC_BASE_URL to your deployment origin."
  );
}

/** Match Vercel's hard ceiling so QStash gives up when the function is killed. */
const DESTINATION_TIMEOUT = "300s";

export async function enqueueScrape(jobId: string): Promise<void> {
  await qstash().publishJSON({
    url: `${baseUrl()}/api/scrape`,
    body: { job_id: jobId },
    retries: 2,
    timeout: DESTINATION_TIMEOUT,
  });
}

/**
 * Enqueue Agent 2 (vision analysis) for exactly one CHECKED slide.
 *
 * Every other place this app publishes "analyze" does so from
 * backend/scrape_poll.py directly (Python, via _lib/queue.py) -- for a plain
 * image post going straight from scraping to analysis. This one exists
 * because a carousel's checked slides are only fanned out to analysis once
 * the USER hits "Continue to analysis" (see
 * app/api/jobs/[id]/continue-slides/route.ts), which is a Next.js route,
 * not a Python stage -- so it needs its own QStash publish from here,
 * mirroring enqueueGenerate's shape. `retries: 2` matches scrape_poll.py's
 * own analyze fan-out: analysis is cheap and safely retryable (unlike
 * generation), so an automatic retry on transient failure is fine here.
 */
export async function enqueueAnalyze(slideRowId: string): Promise<void> {
  await qstash().publishJSON({
    url: `${baseUrl()}/api/analyze`,
    body: { slide_row_id: slideRowId },
    retries: 2,
    timeout: DESTINATION_TIMEOUT,
  });
}

/**
 * Enqueue image generation for exactly one (slide, brand) pair. A post
 * confirmed for both brands gets two of these calls per checked slide --
 * one per brand -- each running independently; the vision/analyze stage
 * never repeats. Keyed by slide rather than post so a carousel's several
 * checked slides can each generate independently -- see
 * migration_006_carousel_slides.sql.
 *
 * `retries: 0` is deliberate. Generation is the only step that costs real
 * money, and a retry after a 300s timeout cannot know whether OpenAI already
 * produced (and billed for) an image. Failing visibly and letting the user
 * press Retry (per-slide, per-brand) is cheaper and more honest than
 * retrying blind.
 *
 * No `deduplicationId` either: the double-click guard is the atomic status
 * transition in Postgres (scoped to this slide+brand), and a dedup id would
 * silently swallow a legitimate Retry of a previously failed brand.
 */
export async function enqueueGenerate(slideRowId: string, brand: string): Promise<void> {
  await qstash().publishJSON({
    url: `${baseUrl()}/api/generate`,
    body: { slide_row_id: slideRowId, brand },
    retries: 0,
    timeout: DESTINATION_TIMEOUT,
  });
}
