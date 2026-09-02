import { NextRequest } from "next/server";
import { supabaseAdmin, userFromRequest, json, UNAUTHORIZED } from "@/lib/supabase-admin";
import { enqueueGenerate } from "@/lib/qstash";

export const runtime = "nodejs";
export const maxDuration = 60;

/**
 * Convenience action: confirm every post in this job that is still awaiting
 * confirmation.
 *
 * This does not weaken the per-post rule -- it is still an explicit, deliberate
 * user action, and it only ever touches posts already parked in
 * `awaiting_confirmation`. Each post is claimed individually and atomically,
 * exactly as the single-post Confirm does.
 */
export async function POST(request: NextRequest, context: { params: Promise<{ id: string }> }) {
  const userId = await userFromRequest(request);
  if (!userId) return UNAUTHORIZED();

  const { id: jobId } = await context.params;
  const sb = supabaseAdmin();

  const { data: pending, error } = await sb
    .from("job_posts")
    .select("id")
    .eq("job_id", jobId)
    .eq("user_id", userId)
    .eq("status", "awaiting_confirmation")
    .order("rank", { ascending: true });

  if (error) return json({ error: error.message }, 500);
  if (!pending?.length) return json({ queued: 0 });

  let queued = 0;
  const failures: string[] = [];

  for (const post of pending) {
    const { data: claimed } = await sb
      .from("job_posts")
      .update({ status: "queued_for_generation", error: null })
      .eq("id", post.id)
      .eq("user_id", userId)
      .eq("status", "awaiting_confirmation")
      .select("id")
      .maybeSingle();

    if (!claimed) continue; // raced with a per-post Confirm; fine.

    try {
      await enqueueGenerate(claimed.id);
      queued += 1;
    } catch (err) {
      await sb
        .from("job_posts")
        .update({
          status: "awaiting_confirmation",
          error: `Could not queue generation: ${(err as Error).message}`,
        })
        .eq("id", claimed.id)
        .eq("user_id", userId);
      failures.push(claimed.id);
    }
  }

  await sb.from("jobs").update({ updated_at: new Date().toISOString() }).eq("id", jobId);

  return json({ queued, failed: failures.length });
}
