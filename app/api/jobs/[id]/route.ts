import { NextRequest } from "next/server";
import { supabaseAdmin, userFromRequest, json, UNAUTHORIZED } from "@/lib/supabase-admin";

export const runtime = "nodejs";
export const maxDuration = 60;

const BUCKET = process.env.NEXT_PUBLIC_SUPABASE_BUCKET || "generated";

/**
 * Delete a job, its posts, and every image it produced.
 *
 * Nothing here is automatic or time-based: results persist until the visitor
 * asks for them to go. This is the only thing that removes them.
 */
export async function DELETE(request: NextRequest, context: { params: Promise<{ id: string }> }) {
  const userId = await userFromRequest(request);
  if (!userId) return UNAUTHORIZED();

  const { id: jobId } = await context.params;
  const sb = supabaseAdmin();

  // Ownership check first -- never let a guessed job id delete someone else's
  // work. Every subsequent query is also scoped to user_id.
  const { data: job } = await sb
    .from("jobs")
    .select("id")
    .eq("id", jobId)
    .eq("user_id", userId)
    .maybeSingle();

  if (!job) return json({ error: "Job not found." }, 404);

  const { data: posts } = await sb
    .from("job_posts")
    .select("thumb_path, final_image_path")
    .eq("job_id", jobId)
    .eq("user_id", userId);

  const paths = (posts ?? [])
    .flatMap((p) => [p.thumb_path, p.final_image_path])
    .filter((p): p is string => Boolean(p));

  if (paths.length) {
    const { error: storageError } = await sb.storage.from(BUCKET).remove(paths);
    // Storage cleanup is best-effort: an orphaned object is far better than a
    // job the user cannot delete.
    if (storageError) console.error("[delete-job] storage cleanup failed:", storageError.message);
  }

  // job_posts rows go with it via ON DELETE CASCADE.
  const { error } = await sb.from("jobs").delete().eq("id", jobId).eq("user_id", userId);
  if (error) return json({ error: `Could not delete the job: ${error.message}` }, 500);

  return json({ deleted: true, images_removed: paths.length });
}
