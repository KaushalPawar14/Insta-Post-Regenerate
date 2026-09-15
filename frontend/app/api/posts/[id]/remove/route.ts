import { NextRequest } from "next/server";
import { supabaseAdmin, userFromRequest, json, UNAUTHORIZED } from "@/lib/supabase-admin";

export const runtime = "nodejs";
export const maxDuration = 15;

const REMOVABLE_STATUSES = ["awaiting_confirmation", "awaiting_slide_selection"];

/**
 * Exclude ONE post from generation entirely.
 *
 * Only ever touches a post sitting in `awaiting_confirmation` OR (for a
 * carousel that hasn't even reached analysis yet) `awaiting_slide_selection`
 * -- the atomic, conditional UPDATE is what makes this safe against a
 * double-click, exactly like the Confirm All route below. `removed` is
 * terminal: Confirm All's own query only ever claims rows still in
 * `awaiting_confirmation`, so a removed post is excluded from generation by
 * construction, not by any extra filtering either route has to remember.
 */
export async function POST(request: NextRequest, context: { params: Promise<{ id: string }> }) {
  const userId = await userFromRequest(request);
  if (!userId) return UNAUTHORIZED();

  const { id: postId } = await context.params;
  const sb = supabaseAdmin();

  const { data: claimed, error } = await sb
    .from("job_posts")
    .update({ status: "removed", error: null })
    .eq("id", postId)
    .eq("user_id", userId)
    .in("status", REMOVABLE_STATUSES)
    .select("id, job_id")
    .maybeSingle();

  if (error) return json({ error: `Could not remove the post: ${error.message}` }, 500);

  if (!claimed) {
    const { data: current } = await sb
      .from("job_posts")
      .select("status")
      .eq("id", postId)
      .eq("user_id", userId)
      .maybeSingle();

    if (!current) return json({ error: "Post not found." }, 404);
    return json(
      { error: `This post is already "${current.status}" and cannot be removed now.` },
      409
    );
  }

  await sb.from("jobs").update({ updated_at: new Date().toISOString() }).eq("id", claimed.job_id);

  return json({ removed: true, post_id: claimed.id });
}
