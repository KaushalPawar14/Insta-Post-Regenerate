import { NextRequest } from "next/server";
import { supabaseAdmin, userFromRequest, json, UNAUTHORIZED } from "@/lib/supabase-admin";
import { enqueueGenerate } from "@/lib/qstash";

export const runtime = "nodejs";
export const maxDuration = 60;

const VALID_BRANDS = new Set(["facts4genius", "factsbytes"]);

/**
 * Confirm every post in this job that is still awaiting confirmation, for
 * EVERY brand in the request body's `brands` array (e.g.
 * ["facts4genius", "factsbytes"]).
 *
 * This does not weaken the per-post rule -- it is still an explicit,
 * deliberate user action, and it only ever touches posts already parked in
 * `awaiting_confirmation`. Each post is claimed individually and atomically,
 * exactly as before multi-brand existed; the only difference (added for
 * carousel support -- see migration_006_carousel_slides.sql) is that
 * confirming now creates one `job_post_brands` row (and publishes one
 * generate message) per SELECTED BRAND per CHECKED SLIDE of the post, not
 * one row per post -- a plain image post has exactly one slide, so its
 * behavior is unchanged. Slides the user left unchecked in stage 2 (never
 * analyzed) are skipped entirely, by construction: the slide query below
 * only ever matches `status = 'analyzed'` rows. The Analyzer is never
 * re-run -- every brand reuses that slide's own
 * `post_slides.image_generation_prompt`/`extracted_text`.
 */
export async function POST(request: NextRequest, context: { params: Promise<{ id: string }> }) {
  const userId = await userFromRequest(request);
  if (!userId) return UNAUTHORIZED();

  const { id: jobId } = await context.params;

  let brands: string[] = [];
  try {
    const body = await request.json();
    brands = Array.isArray(body?.brands) ? body.brands : [];
  } catch {
    // no/invalid body -- brands stays empty, caught by the check below
  }
  brands = Array.from(new Set(brands)).filter((b) => VALID_BRANDS.has(b));
  if (brands.length === 0) {
    return json({ error: "Select at least one format." }, 400);
  }

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

    if (!claimed) continue; // raced with something else; fine.

    const { data: checkedSlides } = await sb
      .from("post_slides")
      .select("id")
      .eq("post_id", claimed.id)
      .eq("status", "analyzed")
      .order("slide_index", { ascending: true });

    let postQueuedAny = false;

    for (const slide of checkedSlides ?? []) {
      for (const brand of brands) {
        const { data: brandRow, error: insertError } = await sb
          .from("job_post_brands")
          .insert({
            slide_id: slide.id,
            post_id: claimed.id,
            job_id: jobId,
            user_id: userId,
            brand,
            status: "queued_for_generation",
          })
          .select("id")
          .maybeSingle();

        if (insertError || !brandRow) {
          failures.push(`${slide.id}:${brand}`);
          continue;
        }

        try {
          await enqueueGenerate(slide.id, brand);
          queued += 1;
          postQueuedAny = true;
        } catch (err) {
          // Isolated to just this slide+brand row -- other slides/brands of
          // the same post may have already enqueued successfully, and must
          // not be reverted because this one failed to queue.
          await sb
            .from("job_post_brands")
            .update({
              status: "failed_generation",
              error: `Could not queue generation: ${(err as Error).message}`,
            })
            .eq("id", brandRow.id);
          failures.push(`${slide.id}:${brand}`);
        }
      }
    }

    if (!postQueuedAny) {
      // Every slide+brand failed to even get queued for this post (or it had
      // no checked slides at all) -- there is now no job_post_brands row
      // that will ever trigger generate.py's rollup, so left as
      // "queued_for_generation" this post would be stuck forever with
      // nothing happening. Revert it to awaiting_confirmation so the user
      // sees it needs confirming again rather than silently hanging.
      await sb
        .from("job_posts")
        .update({
          status: "awaiting_confirmation",
          error: "Could not queue generation for any selected format. Try Confirm & Generate again.",
        })
        .eq("id", claimed.id)
        .eq("user_id", userId);
    }
  }

  await sb.from("jobs").update({ updated_at: new Date().toISOString() }).eq("id", jobId);

  return json({ queued, failed: failures.length });
}
