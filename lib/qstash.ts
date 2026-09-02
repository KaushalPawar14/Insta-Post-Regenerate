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
 * Enqueue image generation for exactly one post.
 *
 * `retries: 0` is deliberate. Generation is the only step that costs real
 * money, and a retry after a 300s timeout cannot know whether OpenAI already
 * produced (and billed for) an image. Failing visibly and letting the user
 * press Retry is cheaper and more honest than retrying blind.
 *
 * No `deduplicationId` either: the double-click guard is the atomic status
 * transition in Postgres, and a dedup id would silently swallow a legitimate
 * Retry of a previously failed post.
 */
export async function enqueueGenerate(postRowId: string): Promise<void> {
  await qstash().publishJSON({
    url: `${baseUrl()}/api/generate`,
    body: { post_row_id: postRowId },
    retries: 0,
    timeout: DESTINATION_TIMEOUT,
  });
}
