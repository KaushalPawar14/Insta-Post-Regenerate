"use client";

import { useEffect, useMemo, useState } from "react";
import { authedFetch, supabaseBrowser } from "@/lib/supabase-browser";
import { useSignedUrl, useGatedImage, BUCKET } from "@/lib/use-job";
import { downloadSlidesAsZip } from "@/lib/slide-download";
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
  type Slide,
} from "@/lib/types";
import PostStepper from "./PostStepper";
import BrandToggle from "./BrandToggle";
import SlideNav from "./SlideNav";

const BADGE_CLASS: Record<JobPost["status"], string> = {
  pending: "badge-idle",
  awaiting_slide_selection: "badge-wait",
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
  slides,
  onChanged,
}: {
  post: JobPost;
  brands: JobPostBrand[];
  slides: Slide[];
  onChanged: () => void;
}) {
  const [caption, setCaption] = useState(post.refined_caption);
  const [dirty, setDirty] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [savedAt, setSavedAt] = useState<number | null>(null);
  const [selectedBrand, setSelectedBrand] = useState<Brand | null>(null);
  const [selectedSlideIndex, setSelectedSlideIndex] = useState(0);

  const removed = post.status === "removed";
  const selectingSlides = post.status === "awaiting_slide_selection";

  const sortedSlides = useMemo(() => [...slides].sort((a, b) => a.slide_index - b.slide_index), [slides]);
  const currentSlide = sortedSlides[selectedSlideIndex] ?? sortedSlides[0] ?? null;

  // Once ANY brand has been confirmed for this post (across ANY of its
  // slides), `brands` is non-empty -- that's the sole signal for "past
  // confirmation," equivalent to (but simpler than) checking post.status
  // against the generation-phase values, since a brand row only ever exists
  // once the post was confirmed for it.
  const confirmed = brands.length > 0;
  const anyCompleted = brands.some((b) => b.status === "completed");

  // This slide's own brand rows -- "also generate for X" / "retry X" now
  // operate per-slide, not per-post, since a carousel's slides each
  // generate independently.
  const currentSlideBrands = useMemo(
    () => (currentSlide ? brands.filter((b) => b.slide_id === currentSlide.id) : []),
    [brands, currentSlide]
  );

  // Pick a brand tab for the current slide, preferring one already
  // completed; PRESERVE the user's choice across slide navigation when that
  // same brand also exists on the newly-selected slide (flipping to the next
  // slide shouldn't reset "I was comparing Facts Bytes" back to whatever
  // finished first), and only fall back to this slide's own default when it
  // doesn't.
  useEffect(() => {
    if (!currentSlide) return;
    if (selectedBrand && currentSlideBrands.some((b) => b.brand === selectedBrand)) return;
    const completed = currentSlideBrands.find((b) => b.status === "completed");
    setSelectedBrand(completed ? completed.brand : currentSlideBrands[0]?.brand ?? null);
  }, [currentSlide, currentSlideBrands, selectedBrand]);

  const currentBrand = currentSlideBrands.find((b) => b.brand === selectedBrand) ?? null;

  // A slide only ever shows generated content once IT (not the post as a
  // whole) was actually checked and successfully analyzed. An unchecked
  // (skipped) slide, or one whose own analysis failed, falls back to its
  // durable original image instead -- permanently, for a skipped slide.
  const showsGeneratedContent = currentSlide?.status === "analyzed";

  const finalUrl = useSignedUrl(showsGeneratedContent ? currentBrand?.final_image_path : null);
  const slideThumbUrl = useSignedUrl(currentSlide?.thumb_path);
  const thumbUrl = useSignedUrl(!confirmed ? post.thumb_path : null);

  // Gates the visible generated <img> behind the browser actually finishing
  // the new brand's image, instead of letting the src swap show a stale
  // frame while it downloads -- see lib/use-job.ts's useGatedImage.
  const { src: gatedSrc, loading: imageLoading } = useGatedImage(
    showsGeneratedContent && currentBrand?.status === "completed" ? finalUrl : null
  );

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

  const generateBrand = (brand: Brand) => {
    if (!currentSlide) return;
    call("generate-" + brand, `/api/slides/${currentSlide.id}/generate-brand`, {
      method: "POST",
      body: JSON.stringify({ brand }),
    });
  };

  async function toggleSlideIncluded(slide: Slide) {
    setBusy("slide-" + slide.id);
    setError(null);
    try {
      const response = await authedFetch(`/api/slides/${slide.id}`, {
        method: "PATCH",
        body: JSON.stringify({ include_in_analysis: !slide.include_in_analysis }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.error || "Could not update this slide.");
      onChanged();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(null);
    }
  }

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

  const downloadUrl = showsGeneratedContent ? finalUrl : slideThumbUrl;
  const downloadEnabled = showsGeneratedContent
    ? currentBrand?.status === "completed" && !!finalUrl
    : !!slideThumbUrl;

  // Every slide's currently-showing image (generated, for the preferred
  // brand if it exists there, else whichever brand IS completed; original,
  // for a skipped/failed-analysis slide) -- the exact same resolution
  // PostCard already uses to decide what the current slide displays,
  // generalized across every slide instead of just the one in view. Only
  // used to build a multi-slide ZIP; a single-image post never calls this
  // (it always has exactly one slide, so the branch below never reaches it).
  async function resolveSlideDownloadSignedUrl(slide: Slide): Promise<string | null> {
    const slideBrands = brands.filter((b) => b.slide_id === slide.id);
    let path: string | null = null;
    if (slide.status === "analyzed") {
      const candidate =
        (selectedBrand && slideBrands.find((b) => b.brand === selectedBrand)) ??
        slideBrands.find((b) => b.status === "completed") ??
        null;
      if (candidate?.status === "completed" && candidate.final_image_path) {
        path = candidate.final_image_path;
      }
    } else {
      path = slide.thumb_path;
    }
    if (!path) return null;
    const { data: signed } = await supabaseBrowser().storage.from(BUCKET).createSignedUrl(path, 3600);
    return signed?.signedUrl ?? null;
  }

  async function download() {
    if (!currentSlide) return;
    setBusy("download");
    setError(null);
    try {
      if (sortedSlides.length > 1) {
        // Multi-slide (carousel) post: a single ZIP, one image per slide in
        // slide order, numbered 1/2/3/... -- browsers can't hand back an
        // actual OS folder, so a ZIP is the standard stand-in.
        const urls = await Promise.all(sortedSlides.map(resolveSlideDownloadSignedUrl));
        await downloadSlidesAsZip(urls);
      } else {
        // Single-image post: EXACT pre-carousel behavior, untouched.
        if (!downloadUrl) return;
        const response = await fetch(downloadUrl);
        if (!response.ok) throw new Error("Could not fetch the image.");
        const blob = await response.blob();
        const objectUrl = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = objectUrl;
        const brandSuffix = showsGeneratedContent && currentBrand ? `_${currentBrand.brand}` : "_original";
        anchor.download = `${post.post_id}_slide${currentSlide.slide_index}${brandSuffix}_final.png`;
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        URL.revokeObjectURL(objectUrl);
      }

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

  const costInr = usdToInr(postCostUsd(post, brands, slides), usdToInrRate());

  // The one brand (if any) with no row at all yet on the CURRENT slide --
  // "Also generate for X." Only meaningful once this slide was actually
  // checked and analyzed; a skipped/failed slide has nothing to generate
  // from.
  const missingBrand =
    showsGeneratedContent ? ALL_BRANDS.find((b) => !currentSlideBrands.some((row) => row.brand === b)) ?? null : null;
  const currentBrandStale = currentBrand ? isStaleBrandGeneration(currentBrand) : false;

  function renderStageMedia() {
    if (!currentSlide) {
      return <div className="media-placeholder">No preview</div>;
    }
    if (!slideThumbUrl) {
      return (
        <div className="media-placeholder">
          <div className="spinner" />
          Preparing preview...
        </div>
      );
    }
    return <img src={slideThumbUrl} alt="" aria-hidden="true" loading="lazy" />;
  }

  function renderConfirmedMedia() {
    if (!currentSlide) {
      return <div className="media-placeholder">No preview</div>;
    }
    if (!showsGeneratedContent) {
      // Skipped in stage 2, or this slide's own analysis failed -- its
      // original scraped image stands in permanently.
      return slideThumbUrl ? (
        <img src={slideThumbUrl} alt={`Original slide ${currentSlide.slide_index + 1}`} loading="lazy" />
      ) : (
        <div className="media-placeholder">No preview</div>
      );
    }
    if (currentBrand?.status === "completed" && gatedSrc) {
      return (
        <div className={`media-image-wrap ${imageLoading ? "loading" : ""}`}>
          <img src={gatedSrc} alt={`Generated post ${post.post_id}`} loading="lazy" />
          {imageLoading && (
            <div className="media-loading-overlay">
              <div className="spinner" />
            </div>
          )}
        </div>
      );
    }
    if (currentBrand?.status === "completed") {
      return (
        <div className="media-placeholder">
          <div className="spinner" />
          Loading image...
        </div>
      );
    }
    if (currentBrand?.status === "failed_generation" || currentBrandStale) {
      return <div className="media-placeholder">{currentBrandStale ? "Timed out" : "Generation failed"}</div>;
    }
    if (currentBrand) {
      return (
        <div className="media-placeholder">
          <div className="spinner" />
          Generating {BRAND_LABELS[currentBrand.brand]}...
        </div>
      );
    }
    // No brand confirmed for this slide at all yet (it was analyzed, but
    // e.g. the OTHER slide is what got confirmed so far, or nothing has
    // reached this slide's brands yet) -- show its own preview.
    return slideThumbUrl ? (
      <img src={slideThumbUrl} alt="" aria-hidden="true" loading="lazy" />
    ) : (
      <div className="media-placeholder">No preview</div>
    );
  }

  return (
    <article className={`post ${removed ? "post-removed" : ""}`}>
      <div
        className={`post-media ${
          !removed && !selectingSlides && (!confirmed || (confirmed && !anyCompleted)) ? "pending" : ""
        }`}
      >
        {removed ? (
          <div className="media-placeholder">Excluded from generation</div>
        ) : selectingSlides ? (
          <SlideNav count={sortedSlides.length} selectedIndex={selectedSlideIndex} onSelect={setSelectedSlideIndex}>
            {renderStageMedia()}
          </SlideNav>
        ) : !confirmed ? (
          thumbUrl ? (
            <>
              <img src={thumbUrl} alt="" aria-hidden="true" loading="lazy" />
              <div className="media-note">
                Original post — shown only for review. The generated image replaces it.
              </div>
            </>
          ) : sortedSlides.length > 0 ? (
            <SlideNav count={sortedSlides.length} selectedIndex={selectedSlideIndex} onSelect={setSelectedSlideIndex}>
              {working && !stale ? (
                <div className="media-placeholder">
                  <div className="spinner" />
                  {currentSlide ? `Analyzing slide ${currentSlide.slide_index + 1}...` : label}
                </div>
              ) : (
                renderStageMedia()
              )}
            </SlideNav>
          ) : (
            <div className="media-placeholder">
              {working && !stale ? <div className="spinner" /> : null}
              {stale ? "Timed out" : working ? label : "No preview"}
            </div>
          )
        ) : (
          <SlideNav count={sortedSlides.length} selectedIndex={selectedSlideIndex} onSelect={setSelectedSlideIndex}>
            {renderConfirmedMedia()}
          </SlideNav>
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

        {!selectingSlides && !removed && confirmed && currentSlideBrands.length > 1 && (
          <BrandToggle brands={currentSlideBrands} selected={selectedBrand ?? currentSlideBrands[0].brand} onSelect={setSelectedBrand} />
        )}

        {post.error && !stale && <div className="post-err">{post.error}</div>}
        {currentBrand?.error && (
          <div className="post-err">
            {BRAND_LABELS[currentBrand.brand]}: {currentBrand.error}
          </div>
        )}
        {currentSlide?.status === "failed_analysis" && !selectingSlides && (
          <div className="post-err">
            Slide {currentSlide.slide_index + 1}: analysis failed
            {currentSlide.error ? ` — ${currentSlide.error}` : ""}. Showing its original image.
          </div>
        )}
        {error && <div className="post-err">{error}</div>}

        {selectingSlides ? (
          <>
            <p className="sub" style={{ margin: 0 }}>
              This post has {sortedSlides.length} slides. Uncheck any you don&apos;t want analyzed or
              generated — unchecked slides cost nothing and keep their original image in the result.
            </p>
            {currentSlide && (
              <label style={{ display: "inline-flex", alignItems: "center", gap: 8, cursor: "pointer" }}>
                <input
                  type="checkbox"
                  checked={currentSlide.include_in_analysis}
                  onChange={() => toggleSlideIncluded(currentSlide)}
                  disabled={busy !== null}
                />
                Include slide {currentSlide.slide_index + 1} in analysis
              </label>
            )}
            <div className="post-actions">
              <button className="btn-danger btn-sm" onClick={remove} disabled={busy !== null}>
                {busy === "remove" ? "Removing..." : "Remove"}
              </button>
            </div>
          </>
        ) : removed ? (
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
                disabled={busy !== null || !downloadEnabled}
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
            {currentSlideBrands
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
                currentSlideBrands
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
