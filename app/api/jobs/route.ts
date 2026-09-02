import { NextRequest } from "next/server";
import { supabaseAdmin, userFromRequest, json, UNAUTHORIZED } from "@/lib/supabase-admin";
import { enqueueScrape } from "@/lib/qstash";
import { parseInstagramInput } from "@/lib/instagram-url";
import { MAX_POSTS_CEILING } from "@/lib/types";

export const runtime = "nodejs";
export const maxDuration = 30;

/**
 * Create a job and kick off the pipeline.
 *
 * The 100-post ceiling is enforced HERE, server-side, and rejected outright
 * rather than silently clamped -- a client sending 5000 gets a 400, not a
 * quietly-trimmed job. (It is also re-checked in the Python scraper and
 * constrained by a CHECK constraint on the table.)
 */
export async function POST(request: NextRequest) {
  const userId = await userFromRequest(request);
  if (!userId) return UNAUTHORIZED();

  let body: { input_url?: unknown; max_posts?: unknown };
  try {
    body = await request.json();
  } catch {
    return json({ error: "Invalid JSON body." }, 400);
  }

  const parsed = parseInstagramInput(String(body.input_url ?? ""));
  if (!parsed.ok) return json({ error: parsed.error }, 400);

  let maxPosts: number;
  if (parsed.inputType === "post") {
    // A single post URL yields exactly one item; scraping and sorting are
    // skipped entirely, so any requested count is meaningless here.
    maxPosts = 1;
  } else {
    const raw = Number(body.max_posts);
    if (!Number.isInteger(raw) || raw < 1) {
      return json({ error: "max_posts must be a whole number of 1 or more." }, 400);
    }
    if (raw > MAX_POSTS_CEILING) {
      return json(
        { error: `max_posts cannot exceed ${MAX_POSTS_CEILING}. You requested ${raw}.` },
        400
      );
    }
    maxPosts = raw;
  }

  const sb = supabaseAdmin();
  const { data, error } = await sb
    .from("jobs")
    .insert({
      user_id: userId,
      input_type: parsed.inputType,
      input_url: parsed.url,
      max_posts: maxPosts,
      status: "pending",
    })
    .select("id")
    .single();

  if (error || !data) {
    return json({ error: `Could not create the job: ${error?.message ?? "unknown error"}` }, 500);
  }

  try {
    await enqueueScrape(data.id);
  } catch (err) {
    // The job row exists but nothing will ever pick it up -- mark it failed
    // rather than leaving a job that looks queued forever.
    await sb
      .from("jobs")
      .update({
        status: "failed",
        error: `Could not enqueue the scrape step: ${(err as Error).message}`,
      })
      .eq("id", data.id);
    return json({ error: `Could not start the job: ${(err as Error).message}` }, 502);
  }

  return json({ job_id: data.id, input_type: parsed.inputType, max_posts: maxPosts }, 201);
}
