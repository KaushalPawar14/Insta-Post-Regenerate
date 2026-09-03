import { NextRequest } from "next/server";
import { supabaseAdmin, bucketName, json } from "@/lib/supabase-admin";

export const runtime = "nodejs";
export const maxDuration = 15;

const SIGNED_URL_TTL_SECONDS = 3600;

/**
 * Public, unauthenticated read for one shared job's finished results.
 *
 * No bearer token, no RLS -- this is the ONE deliberate hole in an otherwise
 * fully private app, and it is scoped as narrowly as possible on purpose:
 *
 *   - Looks up the job by `share_token` ONLY (never by id, never by any other
 *     field), using the service-role client. There is no RLS policy granting
 *     the `anon` role access to `jobs`/`job_posts` at all -- if this route
 *     didn't exist, an anonymous browser client could not read these tables
 *     under any query. The token match is enforced entirely in this server
 *     code, not by a database policy that could accidentally be broadened.
 *   - Returns ONLY: for the job, nothing (its id is used internally and never
 *     serialised into the response); for each post, only `post_id`,
 *     `caption`, and a short-lived signed image URL -- explicitly NOT status,
 *     likes/comments, original_caption, cost fields, error text, or any
 *     other job's data.
 *   - Only posts with status = 'completed' are ever selected. A post still
 *     awaiting confirmation, removed, or failed is invisible here even if
 *     its job_id matches -- there is no way to reach it through this route.
 *   - A token that doesn't match any job returns a generic 404 `not_found`,
 *     not a 500 or a message that could reveal whether e.g. a job exists at
 *     all with a differently-cased or partial token.
 */
export async function GET(_request: NextRequest, context: { params: Promise<{ token: string }> }) {
  const { token } = await context.params;

  if (!token || token.length < 8) {
    return json({ error: "not_found" }, 404);
  }

  const sb = supabaseAdmin();

  const { data: job } = await sb
    .from("jobs")
    .select("id")
    .eq("share_token", token)
    .maybeSingle();

  if (!job) return json({ error: "not_found" }, 404);

  const { data: posts, error } = await sb
    .from("job_posts")
    .select("id, post_id, refined_caption, final_image_path")
    .eq("job_id", job.id)
    .eq("status", "completed")
    .order("rank", { ascending: true });

  if (error) return json({ error: "not_found" }, 404);

  const bucket = bucketName();
  const results = await Promise.all(
    (posts ?? []).map(async (post) => {
      let image_url: string | null = null;
      if (post.final_image_path) {
        const { data: signed } = await sb.storage
          .from(bucket)
          .createSignedUrl(post.final_image_path, SIGNED_URL_TTL_SECONDS);
        image_url = signed?.signedUrl ?? null;
      }
      return {
        id: post.id as string,
        post_id: post.post_id as string,
        caption: (post.refined_caption as string) || "",
        image_url,
      };
    })
  );

  return json({ posts: results });
}
