import { NextRequest } from "next/server";
import { supabaseAdmin, userFromRequest, json, UNAUTHORIZED } from "@/lib/supabase-admin";
import { enqueueGenerate } from "@/lib/qstash";
import { STALE_GENERATION_MS } from "@/lib/types";

export const runtime = "nodejs";
export const maxDuration = 30;

/**
 * Retry a generation that failed, or one that was abandoned when its function
 * hit Vercel's hard 300s ceiling.
 *
 * Because automatic retries are disabled for the paid generate step, this is
 * the deliberate, user-driven way back. It is still a per-post confirmation:
 * pressing Retry is the user accepting the cost again.
 */
export async function POST(request: NextRequest, context: { params: Promise<{ id: string }> }) {
  const userId = await userFromRequest(request);
  if (!userId) return UNAUTHORIZED();

  const { id: postId } = await context.params;
  const sb = supabaseAdmin();

  const { data: post } = await sb
    .from("job_posts")
    .select("id, status, generate_started_at, image_generation_prompt")
    .eq("id", postId)
    .eq("user_id", userId)
    .maybeSingle();

  if (!post) return json({ error: "Post not found." }, 404);

  const stalled =
    post.status === "generating" &&
    post.generate_started_at !== null &&
    Date.now() - new Date(post.generate_started_at).getTime() > STALE_GENERATION_MS;

  const retryable = post.status === "failed_generation" || stalled;
  if (!retryable) {
    return json({ error: `A post with status "${post.status}" cannot be retried.` }, 409);
  }

  if (!post.image_generation_prompt) {
    return json(
      { error: "This post has no analysis result to generate from. Re-run the job instead." },
      409
    );
  }

  const { data: claimed } = await sb
    .from("job_posts")
    .update({
      status: "queued_for_generation",
      error: null,
      generate_started_at: null,
      generate_completed_at: null,
    })
    .eq("id", postId)
    .eq("user_id", userId)
    .eq("status", post.status)
    .select("id")
    .maybeSingle();

  if (!claimed) return json({ error: "The post changed while retrying. Reload and try again." }, 409);

  try {
    await enqueueGenerate(claimed.id);
  } catch (err) {
    await sb
      .from("job_posts")
      .update({ status: "failed_generation", error: `Could not re-queue: ${(err as Error).message}` })
      .eq("id", claimed.id)
      .eq("user_id", userId);
    return json({ error: `Could not re-queue generation: ${(err as Error).message}` }, 502);
  }

  return json({ queued: true });
}
