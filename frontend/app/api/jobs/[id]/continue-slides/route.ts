import { NextRequest } from "next/server";
import { supabaseAdmin, userFromRequest, json, UNAUTHORIZED } from "@/lib/supabase-admin";
import { enqueueAnalyze } from "@/lib/qstash";

export const runtime = "nodejs";
export const maxDuration = 60;

/**
 * "Continue to analysis" -- the batch, job-level action that ends stage 2
 * for every carousel post in this job still sitting in
 * `awaiting_slide_selection`. Mirrors Confirm & Generate's own shape: one
 * explicit action across every eligible post in the job, with per-post
 * checkbox fine-tuning already done on each card beforehand (see
 * PostCard.tsx's stage-2 mode) -- the same "one batch action + per-item
 * escape hatch" idiom this app already uses for Remove + Confirm & Generate.
 *
 * For each such post: claims it atomically (awaiting_slide_selection ->
 * analyzing) exactly like every other stage transition in this app, then
 * fans out one `analyze` message per slide still checked
 * (`include_in_analysis = true`) and marks every unchecked slide `skipped`
 * immediately (no message, no cost, and -- critically -- SlideStatus.SKIPPED
 * is immutable from this point on, which is what makes the caption-
 * promotion race-safety argument in refresh_post_analysis_status hold).
 *
 * A post with ZERO checked slides is left alone (not claimed) and reported
 * back as a per-post error, since there would be nothing to ever move it
 * out of awaiting_slide_selection -- the user can check a slide and retry,
 * or Remove the post instead (now also valid from this status).
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
    .eq("status", "awaiting_slide_selection")
    .order("rank", { ascending: true });

  if (error) return json({ error: error.message }, 500);
  if (!pending?.length) return json({ continued: 0, skipped_posts: 0 });

  let continued = 0;
  let skippedPosts = 0;
  const failures: string[] = [];

  for (const post of pending) {
    const { data: allSlides } = await sb
      .from("post_slides")
      .select("id, include_in_analysis")
      .eq("post_id", post.id)
      .order("slide_index", { ascending: true });

    const checked = (allSlides ?? []).filter((s) => s.include_in_analysis);
    if (checked.length === 0) {
      skippedPosts += 1;
      failures.push(`${post.id}: select at least one slide, or remove this post`);
      continue;
    }

    const { data: claimed } = await sb
      .from("job_posts")
      .update({ status: "analyzing", error: null })
      .eq("id", post.id)
      .eq("user_id", userId)
      .eq("status", "awaiting_slide_selection")
      .select("id")
      .maybeSingle();

    if (!claimed) continue; // raced with something else; fine.

    const unchecked = (allSlides ?? []).filter((s) => !s.include_in_analysis);
    if (unchecked.length) {
      await sb
        .from("post_slides")
        .update({ status: "skipped" })
        .in(
          "id",
          unchecked.map((s) => s.id)
        );
    }

    for (const slide of checked) {
      try {
        await enqueueAnalyze(slide.id);
        continued += 1;
      } catch (err) {
        failures.push(`${slide.id}: ${(err as Error).message}`);
      }
    }
  }

  await sb.from("jobs").update({ updated_at: new Date().toISOString() }).eq("id", jobId);

  return json({ continued, skipped_posts: skippedPosts, failed: failures.length, errors: failures });
}
