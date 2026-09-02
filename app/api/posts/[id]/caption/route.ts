import { NextRequest } from "next/server";
import { supabaseAdmin, userFromRequest, json, UNAUTHORIZED } from "@/lib/supabase-admin";

export const runtime = "nodejs";
export const maxDuration = 15;

const MAX_CAPTION_LENGTH = 2200; // Instagram's own caption limit.

/** Save a lightly-edited caption before the user downloads the post. */
export async function PATCH(request: NextRequest, context: { params: Promise<{ id: string }> }) {
  const userId = await userFromRequest(request);
  if (!userId) return UNAUTHORIZED();

  const { id: postId } = await context.params;

  let body: { refined_caption?: unknown };
  try {
    body = await request.json();
  } catch {
    return json({ error: "Invalid JSON body." }, 400);
  }

  if (typeof body.refined_caption !== "string") {
    return json({ error: "refined_caption must be a string." }, 400);
  }
  if (body.refined_caption.length > MAX_CAPTION_LENGTH) {
    return json(
      { error: `Caption is ${body.refined_caption.length} characters; the limit is ${MAX_CAPTION_LENGTH}.` },
      400
    );
  }

  const { data, error } = await supabaseAdmin()
    .from("job_posts")
    .update({ refined_caption: body.refined_caption })
    .eq("id", postId)
    .eq("user_id", userId)
    .select("id")
    .maybeSingle();

  if (error) return json({ error: error.message }, 500);
  if (!data) return json({ error: "Post not found." }, 404);

  return json({ saved: true });
}
