import { NextRequest } from "next/server";
import { supabaseAdmin, userFromRequest, json, UNAUTHORIZED } from "@/lib/supabase-admin";

export const runtime = "nodejs";
export const maxDuration = 15;

/**
 * Set this post's ENTIRE stage-2 slide selection in one batch call --
 * backs the "Select slides to generate" dialog (SlideSelectDialog.tsx),
 * which shows every slide as a plain numbered checkbox (no images, no
 * per-slide fetch) and submits the whole set on confirm, rather than one
 * PATCH per checkbox toggle. Supersedes the old per-slide
 * `PATCH /api/slides/[id]` route entirely (deleted -- nothing else used it).
 *
 * `selected_indices` are the CHECKED slides' `slide_index` values (0-based,
 * matching post_slides.slide_index) -- every slide whose index is in the
 * list is set `include_in_analysis = true`; every other slide of this post
 * is set `false`. Idempotent and safe to call repeatedly (e.g. reopening
 * the dialog and resubmitting) since it always sets the full state rather
 * than toggling.
 *
 * Only meaningful -- and only allowed -- while the post is still sitting in
 * `awaiting_slide_selection`. Once the user hits "Continue to analysis" the
 * post moves on and the selection is locked in: this immutability is what
 * makes the caption-promotion rule in refresh_post_analysis_status
 * (backend/_lib/pipeline.py) race-safe, and it's also just the correct UX --
 * there is nothing left to "select" once analysis has already started or
 * finished for a slide.
 */
export async function PATCH(request: NextRequest, context: { params: Promise<{ id: string }> }) {
  const userId = await userFromRequest(request);
  if (!userId) return UNAUTHORIZED();

  const { id: postId } = await context.params;

  let body: { selected_indices?: unknown };
  try {
    body = await request.json();
  } catch {
    return json({ error: "Invalid JSON body." }, 400);
  }
  if (!Array.isArray(body.selected_indices) || !body.selected_indices.every((n) => typeof n === "number")) {
    return json({ error: "selected_indices must be an array of numbers." }, 400);
  }
  const selected = new Set(body.selected_indices as number[]);

  const sb = supabaseAdmin();

  const { data: post } = await sb
    .from("job_posts")
    .select("id, status")
    .eq("id", postId)
    .eq("user_id", userId)
    .maybeSingle();

  if (!post) return json({ error: "Post not found." }, 404);
  if (post.status !== "awaiting_slide_selection") {
    return json(
      { error: `This post's slides can no longer be changed (status "${post.status}").` },
      409
    );
  }

  const { data: slides, error: slidesError } = await sb
    .from("post_slides")
    .select("id, slide_index")
    .eq("post_id", postId);

  if (slidesError) return json({ error: slidesError.message }, 500);

  const checkedIds = (slides ?? []).filter((s) => selected.has(s.slide_index)).map((s) => s.id);
  const uncheckedIds = (slides ?? []).filter((s) => !selected.has(s.slide_index)).map((s) => s.id);

  const [checkedResult, uncheckedResult] = await Promise.all([
    checkedIds.length
      ? sb.from("post_slides").update({ include_in_analysis: true }).in("id", checkedIds)
      : Promise.resolve({ error: null }),
    uncheckedIds.length
      ? sb.from("post_slides").update({ include_in_analysis: false }).in("id", uncheckedIds)
      : Promise.resolve({ error: null }),
  ]);

  if (checkedResult.error) return json({ error: checkedResult.error.message }, 500);
  if (uncheckedResult.error) return json({ error: uncheckedResult.error.message }, 500);

  return json({ saved: true, checked: checkedIds.length, unchecked: uncheckedIds.length });
}
