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
 *     the `anon` role access to `jobs`/`job_posts`/`post_slides`/
 *     `job_post_brands` at all -- if this route didn't exist, an anonymous
 *     browser client could not read these tables under any query. The token
 *     match is enforced entirely in this server code, not by a database
 *     policy that could accidentally be broadened.
 *   - Returns ONLY: for the job, nothing (its id is used internally and never
 *     serialised into the response); for each post, `post_id`, `caption`,
 *     and a `slides` array -- explicitly NOT status, likes/comments,
 *     original_caption, cost fields, error text, or any other job's data.
 *   - job_posts.status = 'completed' (the rollup: at least one checked
 *     slide's brand completed, nothing still in flight) selects which POSTS
 *     appear at all -- a post still awaiting confirmation/slide-selection,
 *     removed, or with every checked slide's every brand failed is invisible
 *     here. Within an included post, EVERY slide is returned (so the public
 *     page can show the same slide-nav as the owner's view), each with
 *     either its completed brand image(s) or -- for a slide the owner left
 *     unchecked in stage 2, or whose own analysis failed -- its durable
 *     original image, same "what actually exists" principle applied at the
 *     slide level instead of just the post level. A slide with truly nothing
 *     to show (no completed brand AND no stored original) is dropped.
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
    .select("id, post_id, refined_caption")
    .eq("job_id", job.id)
    .eq("status", "completed")
    .order("rank", { ascending: true });

  if (error) return json({ error: "not_found" }, 404);
  if (!posts?.length) return json({ posts: [] });

  const postIds = posts.map((p) => p.id);
  const [{ data: slideRows }, { data: brandRows }] = await Promise.all([
    sb
      .from("post_slides")
      .select("id, post_id, slide_index, thumb_path")
      .in("post_id", postIds)
      .order("slide_index", { ascending: true }),
    sb
      .from("job_post_brands")
      .select("slide_id, post_id, brand, final_image_path")
      .in("post_id", postIds)
      .eq("status", "completed"),
  ]);

  const bucket = bucketName();

  async function sign(path: string | null): Promise<string | null> {
    if (!path) return null;
    const { data: signed } = await sb.storage.from(bucket).createSignedUrl(path, SIGNED_URL_TTL_SECONDS);
    return signed?.signedUrl ?? null;
  }

  const results = await Promise.all(
    posts.map(async (post) => {
      const mySlides = (slideRows ?? []).filter((s) => s.post_id === post.id);
      const slides = await Promise.all(
        mySlides.map(async (slide) => {
          const myBrandRows = (brandRows ?? []).filter((b) => b.slide_id === slide.id);
          const brands = await Promise.all(
            myBrandRows.map(async (b) => ({
              brand: b.brand as string,
              image_url: await sign(b.final_image_path),
            }))
          );
          const original_image_url = brands.length === 0 ? await sign(slide.thumb_path) : null;
          return {
            slide_index: slide.slide_index as number,
            brands,
            original_image_url,
          };
        })
      );
      return {
        id: post.id as string,
        post_id: post.post_id as string,
        caption: (post.refined_caption as string) || "",
        slides: slides
          .filter((s) => s.brands.length > 0 || s.original_image_url)
          .sort((a, b) => a.slide_index - b.slide_index),
      };
    })
  );

  // Defensive: a post whose rollup says "completed" should always have at
  // least one slide with something to show by construction, but if a race
  // ever left one with none, there's nothing to show for it -- drop it
  // rather than returning an empty card.
  return json({ posts: results.filter((p) => p.slides.length > 0) });
}
