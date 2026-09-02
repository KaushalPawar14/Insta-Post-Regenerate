"use client";

import { useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { authedFetch } from "@/lib/supabase-browser";
import { parseInstagramInput } from "@/lib/instagram-url";
import { MAX_POSTS_CEILING } from "@/lib/types";

export default function NewJobPage() {
  const router = useRouter();
  const [url, setUrl] = useState("");
  // String, not number: a controlled number input backed by a number state
  // fights the user on every keystroke -- clearing the field sends
  // Number("") = 0 straight back into `value`, so it snaps to a literal "0"
  // instead of going empty, and typing after that fights the same snap-back.
  // Keeping the raw text here and only coercing to a number at blur/submit
  // (see handleMaxPostsBlur and submit() below) lets the user freely clear,
  // backspace, and retype without interference.
  const [maxPostsText, setMaxPostsText] = useState("3");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Classified in the browser purely for live feedback. The server re-parses
  // and re-validates everything, including the 100-post ceiling.
  const parsed = useMemo(() => (url.trim() ? parseInstagramInput(url) : null), [url]);
  const isSinglePost = parsed?.ok === true && parsed.inputType === "post";

  // Parsed only at the point it's needed (blur, submit) -- never on every
  // keystroke. parseInt("", 10) and parseInt("-", 10) are both NaN, which is
  // exactly what should make the range check below fail rather than silently
  // coerce to 0.
  function parseMaxPosts(): number {
    return parseInt(maxPostsText, 10);
  }

  // Enforce the range once editing finishes, not while the user is mid-edit --
  // an out-of-range number (e.g. "500") gets visibly clamped; an empty or
  // non-numeric field is left alone so the user can keep typing, and the
  // existing submit-time check catches it with a clear error if they don't.
  function handleMaxPostsBlur() {
    const n = parseMaxPosts();
    if (!Number.isFinite(n)) return;
    const clamped = Math.min(Math.max(n, 1), MAX_POSTS_CEILING);
    setMaxPostsText(String(clamped));
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setError(null);

    const check = parseInstagramInput(url);
    if (!check.ok) {
      setError(check.error);
      return;
    }

    const maxPosts = parseMaxPosts();
    if (check.inputType === "profile" && (!Number.isFinite(maxPosts) || maxPosts < 1 || maxPosts > MAX_POSTS_CEILING)) {
      setError(`Choose between 1 and ${MAX_POSTS_CEILING} posts.`);
      return;
    }

    setSubmitting(true);
    try {
      const response = await authedFetch("/api/jobs", {
        method: "POST",
        body: JSON.stringify({ input_url: url, max_posts: maxPosts }),
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || `Request failed (${response.status}).`);
      router.push(`/job/${payload.job_id}`);
    } catch (err) {
      setError((err as Error).message);
      setSubmitting(false);
    }
  }

  return (
    <>
      <h1>New generation job</h1>
      <p className="sub">
        Give it an Instagram profile to pull top posts from, or a single post URL to work on just
        that one.
      </p>

      {error && (
        <div className="banner banner-err" role="alert">
          {error}
        </div>
      )}

      <form className="card" onSubmit={submit}>
        <div className="field">
          <label htmlFor="url">Instagram URL</label>
          <input
            id="url"
            type="text"
            inputMode="url"
            autoComplete="off"
            placeholder="instagram.com/facts4genius  ·  or  ·  instagram.com/p/SHORTCODE"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            disabled={submitting}
          />
          <div className="hint">
            {parsed === null && "A profile URL, or a /p/ or /reel/ post URL."}
            {parsed?.ok === false && <span style={{ color: "var(--err)" }}>{parsed.error}</span>}
            {parsed?.ok === true && parsed.inputType === "profile" && (
              <>
                Detected a <strong>profile</strong> — posts will be scraped and ranked by likes.
              </>
            )}
            {isSinglePost && (
              <>
                Detected a <strong>single post</strong> — scraping and ranking are skipped entirely.
              </>
            )}
          </div>
        </div>

        {!isSinglePost && (
          <div className="field">
            <label htmlFor="max">Maximum posts to process</label>
            <input
              id="max"
              type="number"
              inputMode="numeric"
              min={1}
              max={MAX_POSTS_CEILING}
              value={maxPostsText}
              onChange={(e) => setMaxPostsText(e.target.value)}
              onBlur={handleMaxPostsBlur}
              disabled={submitting}
            />
            <div className="hint">
              Hard ceiling of {MAX_POSTS_CEILING}, enforced on the server. Each post costs one
              vision call up front; image generation only runs on posts you confirm.
            </div>
          </div>
        )}

        <div className="btn-row" style={{ marginTop: 22 }}>
          <button type="submit" className="btn-primary" disabled={submitting}>
            {submitting ? "Starting..." : isSinglePost ? "Analyze this post" : "Start job"}
          </button>
        </div>
      </form>

      <div className="card">
        <h2>How a run works</h2>
        <ol style={{ color: "var(--text-dim)", fontSize: 13.5, margin: 0, paddingLeft: 20 }}>
          <li>
            <strong style={{ color: "var(--text)" }}>Scrape</strong> — posts are pulled via Apify
            and ranked by likes. Skipped for a single post URL.
          </li>
          <li>
            <strong style={{ color: "var(--text)" }}>Analyze</strong> — a vision model describes the
            visual, transcribes the on-image text, and rewrites the caption.
          </li>
          <li>
            <strong style={{ color: "var(--text)" }}>Your confirmation</strong> — every post stops
            here. Nothing is generated until you press Confirm on it, so no image-generation spend
            happens without you.
          </li>
          <li>
            <strong style={{ color: "var(--text)" }}>Generate</strong> — a fresh branded image is
            produced from the description. You only ever see the new image, never the scraped
            original.
          </li>
        </ol>
      </div>
    </>
  );
}
