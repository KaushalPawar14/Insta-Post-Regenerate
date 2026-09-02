import { NextRequest } from "next/server";
import { supabaseAdmin, userFromRequest, json, UNAUTHORIZED } from "@/lib/supabase-admin";
import { enqueueGenerate } from "@/lib/qstash";

export const runtime = "nodejs";
export const maxDuration = 30;

/**
 * Confirm ONE post for image generation.
 *
 * This is the only path in the entire app that can trigger a paid image
 * generation. The Analyzer never queues generation itself -- a post sits in
 * `awaiting_confirmation` indefinitely until a human presses this button.
 *
 * The status transition is conditional (`.eq("status", "awaiting_confirmation")`),
 * which makes it atomic in Postgres. A double-click therefore updates zero
 * rows on the second attempt and never publishes a second message.
 */
export async function POST(request: NextRequest, context: { params: Promise<{ id: string }> }) {
  const userId = await userFromRequest(request);
  if (!userId) return UNAUTHORIZED();

  const { id: postId } = await context.params;
  const sb = supabaseAdmin();

  const { data: claimed, error } = await sb
    .from("job_posts")
    .update({ status: "queued_for_generation", error: null })
    .eq("id", postId)
    .eq("user_id", userId)
    .eq("status", "awaiting_confirmation")
    .select("id, job_id")
    .maybeSingle();

  if (error) return json({ error: `Could not confirm the post: ${error.message}` }, 500);

  if (!claimed) {
    // Either it isn't yours, it doesn't exist, or it already moved on.
    const { data: current } = await sb
      .from("job_posts")
      .select("status")
      .eq("id", postId)
      .eq("user_id", userId)
      .maybeSingle();

    if (!current) return json({ error: "Post not found." }, 404);
    return json(
      { error: `This post is already "${current.status}" and cannot be confirmed again.` },
      409
    );
  }

  try {
    await enqueueGenerate(claimed.id);
  } catch (err) {
    await sb
      .from("job_posts")
      .update({
        status: "awaiting_confirmation",
        error: `Could not queue generation: ${(err as Error).message}`,
      })
      .eq("id", claimed.id)
      .eq("user_id", userId);
    return json({ error: `Could not queue generation: ${(err as Error).message}` }, 502);
  }

  await sb.from("jobs").update({ updated_at: new Date().toISOString() }).eq("id", claimed.job_id);

  return json({ queued: true, post_id: claimed.id });
}
