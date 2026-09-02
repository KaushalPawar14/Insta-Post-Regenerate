import { NextRequest } from "next/server";
import { supabaseAdmin, userFromRequest, json, UNAUTHORIZED } from "@/lib/supabase-admin";

export const runtime = "nodejs";
export const maxDuration = 15;

/**
 * Record that the user has downloaded this post's generated image.
 *
 * Once every post in a job is marked downloaded, the job page shows a gentle
 * suggestion to delete it. That is only ever a suggestion -- nothing is
 * removed automatically, on a timer or otherwise.
 */
export async function POST(request: NextRequest, context: { params: Promise<{ id: string }> }) {
  const userId = await userFromRequest(request);
  if (!userId) return UNAUTHORIZED();

  const { id: postId } = await context.params;

  const { data, error } = await supabaseAdmin()
    .from("job_posts")
    .update({ downloaded: true })
    .eq("id", postId)
    .eq("user_id", userId)
    .select("id")
    .maybeSingle();

  if (error) return json({ error: error.message }, 500);
  if (!data) return json({ error: "Post not found." }, 404);

  return json({ downloaded: true });
}
