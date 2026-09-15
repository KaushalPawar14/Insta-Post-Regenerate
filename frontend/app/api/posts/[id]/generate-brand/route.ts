import { NextRequest } from "next/server";
import { supabaseAdmin, userFromRequest, json, UNAUTHORIZED } from "@/lib/supabase-admin";
import { enqueueGenerate } from "@/lib/qstash";

export const runtime = "nodejs";
export const maxDuration = 30;

const VALID_BRANDS = new Set(["facts4genius", "factsbytes"]);

/**
 * Queue generation for ONE brand on a post that has already been confirmed.
 * Unified route for two cases that turn out to be the same operation:
 *
 *   - "Also generate for <brand>" -- this brand has no job_post_brands row
 *     yet (the post was confirmed for the OTHER brand only). Inserts a new
 *     row at queued_for_generation.
 *   - "Retry <brand>" -- a row exists and is `failed_generation` (including
 *     one abandoned past the 300s ceiling, which claim_brand_generation
 *     marks failed_generation for exactly this reason). Atomically claims
 *     it back to queued_for_generation.
 *
 * Either way: reuses the post's EXISTING image_generation_prompt/
 * extracted_text (Agent 2's output) -- this route never touches analysis,
 * never re-scrapes, and incurs no new Apify or vision cost. Only the one
 * new images.edit call's cost (added when generate.py completes) is new.
 *
 * A brand that's queued/generating/completed already cannot be touched here
 * -- there is nothing to (re)start.
 */
export async function POST(request: NextRequest, context: { params: Promise<{ id: string }> }) {
  const userId = await userFromRequest(request);
  if (!userId) return UNAUTHORIZED();

  const { id: postId } = await context.params;

  let brand = "";
  try {
    const body = await request.json();
    brand = typeof body?.brand === "string" ? body.brand : "";
  } catch {
    // brand stays empty, caught below
  }
  if (!VALID_BRANDS.has(brand)) {
    return json({ error: "Invalid or missing brand." }, 400);
  }

  const sb = supabaseAdmin();

  const { data: post } = await sb
    .from("job_posts")
    .select("id, job_id, status, image_generation_prompt")
    .eq("id", postId)
    .eq("user_id", userId)
    .maybeSingle();

  if (!post) return json({ error: "Post not found." }, 404);

  // Only meaningful once the post has been confirmed at all (it has been
  // through Confirm & Generate for at least the other brand already).
  const confirmedStatuses = ["queued_for_generation", "generating", "completed", "failed_generation"];
  if (!confirmedStatuses.includes(post.status)) {
    return json({ error: `This post hasn't been confirmed yet (status "${post.status}").` }, 409);
  }
  if (!post.image_generation_prompt) {
    return json(
      { error: "This post has no analysis result to generate from. Re-run the job instead." },
      409
    );
  }

  const { data: existing } = await sb
    .from("job_post_brands")
    .select("id, status")
    .eq("post_id", postId)
    .eq("brand", brand)
    .maybeSingle();

  let brandRowId: string;

  if (existing) {
    if (existing.status !== "failed_generation") {
      return json({ error: `${brand} is already "${existing.status}" for this post.` }, 409);
    }
    const { data: claimed } = await sb
      .from("job_post_brands")
      .update({ status: "queued_for_generation", error: null, generate_started_at: null, generate_completed_at: null })
      .eq("id", existing.id)
      .eq("status", "failed_generation")
      .select("id")
      .maybeSingle();

    if (!claimed) {
      return json({ error: "This brand changed while retrying. Reload and try again." }, 409);
    }
    brandRowId = claimed.id;
  } else {
    const { data: inserted, error: insertError } = await sb
      .from("job_post_brands")
      .insert({ post_id: postId, job_id: post.job_id, user_id: userId, brand, status: "queued_for_generation" })
      .select("id")
      .maybeSingle();

    if (insertError || !inserted) {
      return json({ error: insertError?.message || "Could not create the generation record." }, 500);
    }
    brandRowId = inserted.id;
  }

  try {
    await enqueueGenerate(postId, brand);
  } catch (err) {
    await sb
      .from("job_post_brands")
      .update({ status: "failed_generation", error: `Could not queue: ${(err as Error).message}` })
      .eq("id", brandRowId);
    return json({ error: `Could not queue generation: ${(err as Error).message}` }, 502);
  }

  await sb.from("jobs").update({ updated_at: new Date().toISOString() }).eq("id", post.job_id);

  return json({ queued: true, brand });
}
