import { NextRequest } from "next/server";
import { supabaseAdmin, userFromRequest, json, UNAUTHORIZED } from "@/lib/supabase-admin";

export const runtime = "nodejs";
export const maxDuration = 15;

/**
 * Toggle one slide's stage-2 checkbox (`include_in_analysis`).
 *
 * Only meaningful -- and only allowed -- while the PARENT POST is still
 * sitting in `awaiting_slide_selection`. Once the user hits "Continue to
 * analysis" the post moves on and every slide's selection is locked in:
 * this immutability is what makes the caption-promotion rule in
 * refresh_post_analysis_status (backend/_lib/pipeline.py) race-safe, and
 * it's also just the correct UX -- there is nothing left to "select" once
 * analysis has already started or finished for a slide.
 */
export async function PATCH(request: NextRequest, context: { params: Promise<{ id: string }> }) {
  const userId = await userFromRequest(request);
  if (!userId) return UNAUTHORIZED();

  const { id: slideId } = await context.params;

  let body: { include_in_analysis?: unknown };
  try {
    body = await request.json();
  } catch {
    return json({ error: "Invalid JSON body." }, 400);
  }
  if (typeof body.include_in_analysis !== "boolean") {
    return json({ error: "include_in_analysis must be a boolean." }, 400);
  }

  const sb = supabaseAdmin();

  const { data: slide } = await sb
    .from("post_slides")
    .select("id, post_id")
    .eq("id", slideId)
    .eq("user_id", userId)
    .maybeSingle();

  if (!slide) return json({ error: "Slide not found." }, 404);

  const { data: post } = await sb
    .from("job_posts")
    .select("status")
    .eq("id", slide.post_id)
    .eq("user_id", userId)
    .maybeSingle();

  if (!post) return json({ error: "Parent post not found." }, 404);
  if (post.status !== "awaiting_slide_selection") {
    return json(
      { error: `This post's slides can no longer be changed (status "${post.status}").` },
      409
    );
  }

  const { error } = await sb
    .from("post_slides")
    .update({ include_in_analysis: body.include_in_analysis })
    .eq("id", slideId);

  if (error) return json({ error: error.message }, 500);

  return json({ saved: true });
}
