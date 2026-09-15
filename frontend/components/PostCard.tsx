"use client";

import { useEffect, useState } from "react";
import { authedFetch } from "@/lib/supabase-browser";
import { useSignedUrl } from "@/lib/use-job";
import {
  ALL_BRANDS,
  BRAND_LABELS,
  STAGE_LABELS,
  isStaleGeneration,
  isStaleBrandGeneration,
  postCostUsd,
  usdToInr,
  usdToInrRate,
  formatInr,
  type Brand,
  type JobPost,
  type JobPostBrand,
} from "@/lib/types";
import PostStepper from "./PostStepper";
import BrandToggle from "./BrandToggle";

const BADGE_CLASS: Record<JobPost["status"], string> = {
  pending: "badge-idle",
  analyzing: "badge-work",
  awaiting_confirmation: "badge-wait",
  queued_for_generation: "badge-work",
  generating: "badge-work",
  completed: "badge-done",
  failed_analysis: "badge-err",
  failed_generation: "badge-err",
  removed: "badge-idle",
};

export default function PostCard({
  post,
  brands,
  onChanged,
}: {
  post: JobPost;
  brands: JobPostBrand[];
  onChanged: () => void;
}) {
  const [caption, setCaption] = useState(post.refined_caption);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const [selectedBrand, setSelectedBrand] = useState<Brand | null>(null);

  const removed = post.status === "removed";

  // Once ANY brand has been confirmed for this post, `brands` is non-empty --
  // that's the sole signal for "past confirmation," equivalent to (but
  // simpler than) checking post.status against the generation-phase values,
  // since a brand row only ever exists once the post was confirmed for it.
  const confirmed = brands.length > 0;
  const anyCompleted = brands.some((b) => b.status === "completed");

  // Pick a default tab once brands appear, preferring a completed one (so
  // there's something to look at); never override an explicit or
  // already-picked selection afterward, even as the other brand's status
  // changes later -- switching the image out from under someone mid-review
  // would be worse than leaving their choice alone.
  useEffect(() => {
    if (selectedBrand || brands.length === 0) return;
    const completed = brands.find((b) => b.status === "completed");
    setSelectedBrand((completed ?? brands[0]).brand);
  }, [brands, selectedBrand]);

  const currentBrand = brands.find((b) => b.brand === selectedBrand) ?? null;

  // Show the generated image once the selected brand has one; before any
  // brand is confirmed, the original thumbnail is shown ONLY so the user
  // knows what they are confirming.
  const finalUrl = useSignedUrl(currentBrand?.final_image_path);
  const thumbUrl = useSignedUrl(confirmed ? null : post.thumb_path);

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

  const remove = () => {
    if (!window.confirm("Exclude this post from generation? It won't be generated even by Confirm & Generate.")) return;
    call("remove", `/api/posts/${post.id}/remove`, { method: "POST" });
  };

  const generateBrand = (brand: Brand) =>
    call("generate-" + brand, `/api/posts/${post.id}/generate-brand`, {
      method: "POST",
      body: JSON.stringify({ brand }),
    });

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
    if (!finalUrl || !currentBrand) return;
    setBusy("download");
    setError(null);
    try {
      const response = await fetch(finalUrl);
      if (!response.ok) throw new Error("Could not fetch the image.");
      const blob = await response.blob();
      const objectUrl = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = objectUrl;
      anchor.download = `${post.post_id}_${currentBrand.brand}_final.png`;
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

  const costInr = usdToInr(postCostUsd(post, brands), usdToInrRate());

  // The one brand (if any) with no row at all yet -- "Also generate for X".
  const missingBrand = ALL_BRANDS.find((b) => !brands.some((row) => row.brand === b)) ?? null;
  const currentBrandStale = currentBrand ? isStaleBrandGeneration(currentBrand) : false;

  return (
    <article className={`post ${removed ? "post-removed" : ""}`}>
      <div className={`post-media ${!removed && (!confirmed || (confirmed && !anyCompleted)) ? "pending" : ""}`}>
        {removed ? (
          <div className="media-placeholder">Excluded from generation</div>
        ) : !confirmed ? (
          thumbUrl ? (
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
          )
        ) : currentBrand?.status === "completed" && finalUrl ? (
          <img src={finalUrl} alt={`Generated post ${post.post_id}`} loading="lazy" />
        ) : currentBrand?.status === "completed" ? (
          <div className="media-placeholder">
            <div className="spinner" />
            Loading image...
          </div>
        ) : currentBrand?.status === "failed_generation" || currentBrandStale ? (
          <div className="media-placeholder">{currentBrandStale ? "Timed out" : "Generation failed"}</div>
        ) : (
          <div className="media-placeholder">
            <div className="spinner" />
            {currentBrand ? `Generating ${BRAND_LABELS[currentBrand.brand]}...` : "Generating..."}
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

        <PostStepper status={post.status} />

        {brands.length > 1 && selectedBrand && (
          <BrandToggle brands={brands} selected={selectedBrand} onSelect={setSelectedBrand} />
        )}

        {post.error && !stale && <div className="post-err">{post.error}</div>}
        {currentBrand?.error && (
          <div className="post-err">
            {BRAND_LABELS[currentBrand.brand]}: {currentBrand.error}
          </div>
        )}
        {error && <div className="post-err">{error}</div>}

        {removed ? (
          <div className="removed-strip">
            This post was excluded before confirmation. It will never be generated.
          </div>
        ) : anyCompleted ? (
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

            <div className="cost-row">
              Estimated cost:
              <span className="cost-value">{formatInr(costInr)}</span>
              <span className="cost-estimate-tag">est.</span>
            </div>

            <div className="post-actions">
              <button
                className="btn-primary btn-sm"
                onClick={download}
                disabled={busy !== null || currentBrand?.status !== "completed" || !finalUrl}
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

            {missingBrand && (
              <div className="post-actions">
                <button
                  className="btn-secondary btn-sm"
                  onClick={() => generateBrand(missingBrand)}
                  disabled={busy !== null}
                >
                  {busy === "generate-" + missingBrand
                    ? "Queuing..."
                    : `Also generate for ${BRAND_LABELS[missingBrand]}`}
                </button>
              </div>
            )}
            {brands
              .filter((b) => b.status === "failed_generation" || isStaleBrandGeneration(b))
              .map((b) => (
                <div className="post-actions" key={b.brand}>
                  <button
                    className="btn-secondary btn-sm"
                    onClick={() => generateBrand(b.brand)}
                    disabled={busy !== null}
                  >
                    {busy === "generate-" + b.brand ? "Retrying..." : `Retry ${BRAND_LABELS[b.brand]}`}
                  </button>
                </div>
              ))}
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
                <button className="btn-danger btn-sm" onClick={remove} disabled={busy !== null}>
                  {busy === "remove" ? "Removing..." : "Remove"}
                </button>
              )}
              {confirmed &&
                brands
                  .filter((b) => b.status === "failed_generation" || isStaleBrandGeneration(b))
                  .map((b) => (
                    <button
                      key={b.brand}
                      className="btn-secondary btn-sm"
                      onClick={() => generateBrand(b.brand)}
                      disabled={busy !== null}
                    >
                      {busy === "generate-" + b.brand ? "Retrying..." : `Retry ${BRAND_LABELS[b.brand]}`}
                    </button>
                  ))}
            </div>
          </>
        )}
      </div>
    </article>
  );
}
