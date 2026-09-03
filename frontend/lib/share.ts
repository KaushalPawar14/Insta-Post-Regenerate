import { authedFetch } from "./supabase-browser";

/**
 * Create (or fetch the existing) public share link for a job the caller
 * owns. See app/api/jobs/[id]/share/route.ts -- idempotent, owner-only.
 */
export async function createShareLink(jobId: string): Promise<string> {
  const response = await authedFetch(`/api/jobs/${jobId}/share`, { method: "POST" });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || "Could not create a share link.");
  return payload.share_url as string;
}
