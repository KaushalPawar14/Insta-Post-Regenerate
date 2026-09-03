import { randomUUID } from "crypto";
import { NextRequest } from "next/server";
import { supabaseAdmin, userFromRequest, json, UNAUTHORIZED } from "@/lib/supabase-admin";

export const runtime = "nodejs";
export const maxDuration = 15;

/**
 * Create (or return the existing) public share link for one job.
 *
 * Owner-only: requires a valid bearer token, and the lookup is scoped to
 * `user_id = caller`, so a visitor can only ever mint a share link for their
 * OWN job -- never someone else's.
 *
 * The token is a fresh crypto.randomUUID() (122 bits of randomness), never
 * the job's own database id -- a share link must not double as a way to
 * guess or enumerate real job ids. Idempotent: calling this again for a job
 * that already has a token just returns it unchanged, so double-clicking
 * Share can never produce two different links for the same job. The write
 * is conditioned on `share_token is null`, the same atomic-claim pattern
 * used elsewhere in this codebase, so two rapid clicks can race without
 * either one clobbering the other's result.
 */
export async function POST(request: NextRequest, context: { params: Promise<{ id: string }> }) {
  const userId = await userFromRequest(request);
  if (!userId) return UNAUTHORIZED();

  const { id: jobId } = await context.params;
  const sb = supabaseAdmin();

  const { data: job, error } = await sb
    .from("jobs")
    .select("id, share_token")
    .eq("id", jobId)
    .eq("user_id", userId)
    .maybeSingle();

  if (error) return json({ error: error.message }, 500);
  if (!job) return json({ error: "Job not found." }, 404);

  let token = job.share_token as string | null;

  if (!token) {
    const candidate = randomUUID();
    const { data: claimed } = await sb
      .from("jobs")
      .update({ share_token: candidate })
      .eq("id", jobId)
      .eq("user_id", userId)
      .is("share_token", null)
      .select("share_token")
      .maybeSingle();

    if (claimed) {
      token = claimed.share_token as string;
    } else {
      // Lost the race to a concurrent click -- read back whichever token won.
      const { data: current } = await sb
        .from("jobs")
        .select("share_token")
        .eq("id", jobId)
        .eq("user_id", userId)
        .maybeSingle();
      token = (current?.share_token as string | null) ?? candidate;
    }
  }

  const explicitBase = process.env.PUBLIC_BASE_URL?.trim().replace(/\/$/, "");
  const baseUrl = explicitBase || `https://${request.headers.get("host")}`;

  return json({ share_token: token, share_url: `${baseUrl}/share/${token}` });
}
