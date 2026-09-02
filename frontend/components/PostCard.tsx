"use client";

import { useEffect, useState } from "react";
import { authedFetch } from "@/lib/supabase-browser";
import { useSignedUrl } from "@/lib/use-job";
import { STAGE_LABELS, isStaleGeneration, type JobPost } from "@/lib/types";

const BADGE_CLASS: Record<JobPost["status"], string> = {
  pending: "badge-idle",
  analyzing: "badge-work",
  awaiting_confirmation: "badge-wait",
  queued_for_generation: "badge-work",
  generating: "badge-work",
  completed: "badge-done",
  failed_analysis: "badge-err",
  failed_generation: "badge-err",
};

export default function PostCard({ post, onChanged }: { post: JobPost; onChanged: () => void }) {
  const [caption, setCaption] = useState(post.refined_caption);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);

  // Show the generated image once it exists; before that, the original
  // thumbnail is shown ONLY so the user knows what they are confirming.
  const finalUrl = useSignedUrl(post.final_image_path);
  const thumbUrl = useSignedUrl(post.status === "completed" ? null : post.thumb_path);

  // Adopt server-side caption changes, but never clobber an unsaved edit.
  useEffect(() => {
    if (!dirty) setCaption(post.refined_caption);
  }, [post.refined_caption, dirty]);

  const stale = isStaleGeneration(post);
  const label = stale ? "Generation timed out" : STAGE_LABELS[post.status];

  async function call(action: string, path: string, init?: RequestInit) {
    setBusy(action);
    setError(null);
    try {
      const response = await authedFetch(path, init);
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.error || `Request failed (${response.status}).`);
      onChanged();
      return true;
    } catch (err) {
      setError((err as Error).message);
      return false;
    } finally {
      setBusy(null);
    }
  }

  const confirm = () => call("confirm", `/api/posts/${post.id}/confirm`, { method: "POST" });
  const retry = () => call("retry", `/api/posts/${post.id}/retry`, { method: "POST" });

  async function saveCaption() {
    const ok = await call("save", `/api/posts/${post.id}/caption`, {
      method: "PATCH",
      body: JSON.stringify({ refined_caption: caption }),
    });
    if (ok) {
      setDirty(false);
      setSavedAt(Date.now());
    }
  }

  async function download() {
    if (!finalUrl) return;
    setBusy("download");
    setError(null);
    try {
      const response = await fetch(finalUrl);
      if (!response.ok) throw new Error("Could not fetch the image.");
      const blob = await response.blob();
      const objectUrl = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = objectUrl;
      anchor.download = `${post.post_id}_final.png`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(objectUrl);

      // Feeds the "everything downloaded -- you can delete this job" hint.
      await authedFetch(`/api/posts/${post.id}/downloaded`, { method: "POST" });
      onChanged();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function copyCaption() {
    try {
      await navigator.clipboard.writeText(caption);
      setSavedAt(Date.now());
    } catch {
      setError("Clipboard access was blocked by the browser.");
    }
  }

  const working = ["pending", "analyzing", "queued_for_generation", "generating"].includes(
    post.status
  );

  return (
    <article className="post">
      <div className={`post-media ${post.status === "completed" ? "" : "pending"}`}>
        {post.status === "completed" && finalUrl ? (
          <img src={finalUrl} alt={`Generated post ${post.post_id}`} loading="lazy" />
        ) : post.status === "completed" ? (
          <div className="media-placeholder">
            <div className="spinner" />
            Loading image...
          </div>
        ) : thumbUrl ? (
          <>
            <img src={thumbUrl} alt="" aria-hidden="true" loading="lazy" />
            <div className="media-note">
              Original post — shown only for review. The generated image replaces it.
            </div>
          </>
        ) : (
          <div className="media-placeholder">
            {working && !stale ? <div className="spinner" /> : null}
            {stale ? "Timed out" : working ? label : "No preview"}
          </div>
        )}
      </div>

      <div className="post-body">
        <div className="post-head">
          <span className={`badge ${stale ? "badge-err" : BADGE_CLASS[post.status]}`}>{label}</span>
          <span className="post-meta">
            {post.likes.toLocaleString()} likes · {post.comments.toLocaleString()} comments
          </span>
        </div>

        {post.error && !stale && <div className="post-err">{post.error}</div>}
        {error && <div className="post-err">{error}</div>}

        {post.status === "completed" ? (
          <>
            <label className="caption-label" htmlFor={`cap-${post.id}`}>
              Caption — edit before downloading
            </label>
            <textarea
              id={`cap-${post.id}`}
              value={caption}
              onChange={(e) => {
                setCaption(e.target.value);
                setDirty(true);
              }}
              rows={5}
            />
            <div className="post-actions">
              <button
                className="btn-primary btn-sm"
                onClick={download}
                disabled={busy !== null || !finalUrl}
              >
                {busy === "download" ? "Downloading..." : post.downloaded ? "Download again" : "Download"}
              </button>
              <button
                className="btn-secondary btn-sm"
                onClick={saveCaption}
                disabled={busy !== null || !dirty}
              >
                {busy === "save" ? "Saving..." : dirty ? "Save caption" : "Saved"}
              </button>
              <button className="btn-ghost btn-sm" onClick={copyCaption} disabled={busy !== null}>
                Copy
              </button>
              {savedAt && !dirty && (
                <span style={{ fontSize: 12, color: "var(--ok)" }}>Done</span>
              )}
            </div>
          </>
        ) : (
          <>
            {post.refined_caption ? (
              <>
                <span className="caption-label">Rewritten caption</span>
                <div className="caption-preview">{post.refined_caption}</div>
              </>
            ) : (
              <div className="caption-preview" style={{ color: "var(--text-faint)" }}>
                {post.original_caption
                  ? `Original: ${post.original_caption.slice(0, 180)}${post.original_caption.length > 180 ? "..." : ""}`
                  : "No caption on the original post."}
              </div>
            )}

            <div className="post-actions">
              {post.status === "awaiting_confirmation" && (
                <button className="btn-primary btn-sm" onClick={confirm} disabled={busy !== null}>
                  {busy === "confirm" ? "Queuing..." : "Confirm & generate"}
                </button>
              )}
              {(post.status === "failed_generation" || stale) && (
                <button className="btn-secondary btn-sm" onClick={retry} disabled={busy !== null}>
                  {busy === "retry" ? "Retrying..." : "Retry generation"}
                </button>
              )}
            </div>
          </>
        )}
      </div>
    </article>
  );
}
