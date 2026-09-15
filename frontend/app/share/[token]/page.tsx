"use client";

import { use, useEffect, useMemo, useState } from "react";
import BrandToggle from "@/components/BrandToggle";
import SlideNav from "@/components/SlideNav";
import { useGatedImage } from "@/lib/use-job";
import { downloadSlidesAsZip } from "@/lib/slide-download";
import type { Brand } from "@/lib/types";

interface SharedBrandImage {
  brand: Brand;
  image_url: string | null;
}

interface SharedSlide {
  slide_index: number;
  brands: SharedBrandImage[];
  original_image_url: string | null;
}

interface SharedPost {
  id: string;
  post_id: string;
  caption: string;
  slides: SharedSlide[];
}

/**
 * Public, read-only view of one job's finished results. No auth, no
 * SessionBoot (see components/SessionBoot.tsx), no Realtime -- it fetches
 * once from the unauthenticated /api/share/[token] route, which is the only
 * thing standing between a visitor and this data. There is nothing here to
 * confirm, remove, or delete; every action is read-only (view, download,
 * copy caption). Slide navigation (SlideNav) and the brand toggle
 * (BrandToggle) are the exact same components PostCard uses, per the
 * requirement that every view reuse the same display components.
 */
export default function SharePage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = use(params);
  const [posts, setPosts] = useState<SharedPost[] | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch(`/api/share/${token}`)
      .then(async (response) => {
        if (cancelled) return;
        if (response.status === 404) {
          setNotFound(true);
          return;
        }
        if (!response.ok) throw new Error("Could not load this share link.");
        const payload = await response.json();
        setPosts(payload.posts ?? []);
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  if (notFound) {
    return (
      <div className="empty">This share link doesn&apos;t exist, or has been removed.</div>
    );
  }

  if (error) {
    return <div className="banner banner-err">{error}</div>;
  }

  if (posts === null) {
    return (
      <div className="empty">
        <div className="spinner" style={{ margin: "0 auto 12px" }} />
        Loading shared results...
      </div>
    );
  }

  return (
    <>
      <h1>Shared results</h1>
      <p className="sub">
        {posts.length} generated post{posts.length === 1 ? "" : "s"} -- view, download, or copy the
        caption below.
      </p>

      {posts.length === 0 ? (
        <div className="card">
          <div className="empty">No completed results yet. Check back later.</div>
        </div>
      ) : (
        <div className="post-grid">
          {posts.map((post) => (
            <SharedPostCard key={post.id} post={post} />
          ))}
        </div>
      )}
    </>
  );
}

function SharedPostCard({ post }: { post: SharedPost }) {
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [selectedSlideIndex, setSelectedSlideIndex] = useState(0);
  const [selectedBrand, setSelectedBrand] = useState<Brand | null>(null);

  const sortedSlides = useMemo(() => [...post.slides].sort((a, b) => a.slide_index - b.slide_index), [post.slides]);
  const currentSlide = sortedSlides[selectedSlideIndex] ?? sortedSlides[0] ?? null;

  // Preserve the brand choice across slide navigation when it still exists
  // on the newly-selected slide, same as PostCard.tsx; otherwise fall back
  // to that slide's own default (completed first, else whatever's there).
  useEffect(() => {
    if (!currentSlide) return;
    if (selectedBrand && currentSlide.brands.some((b) => b.brand === selectedBrand)) return;
    setSelectedBrand(currentSlide.brands[0]?.brand ?? null);
  }, [currentSlide, selectedBrand]);

  const current = currentSlide?.brands.find((b) => b.brand === selectedBrand) ?? currentSlide?.brands[0] ?? null;
  const imageUrl = current?.image_url ?? currentSlide?.original_image_url ?? null;

  // Same load-gating PostCard.tsx uses: keep the last-loaded image on screen
  // (dimmed) with a spinner overlay until the newly toggled brand's / newly
  // navigated slide's image has actually finished loading, rather than
  // letting the <img> src swap show a stale frame while it downloads.
  const { src: gatedSrc, loading: imageLoading } = useGatedImage(imageUrl);

  // Same fetch -> blob -> object URL -> anchor mechanism PostCard.tsx uses,
  // minus the authenticated `.../downloaded` call at the end -- that marks
  // the OWNER's post as downloaded (feeds their private "delete this job?"
  // nudge) and requires a bearer token neither present nor appropriate here.
  // Downloads whichever slide+brand is currently being viewed -- or, for a
  // multi-slide post, every slide's currently-showing image bundled into one
  // ZIP (see resolveSharedSlideUrl below and lib/slide-download.ts).
  function resolveSharedSlideUrl(slide: SharedSlide): string | null {
    if (slide.brands.length > 0) {
      const candidate = (selectedBrand && slide.brands.find((b) => b.brand === selectedBrand)) ?? slide.brands[0];
      return candidate?.image_url ?? null;
    }
    return slide.original_image_url;
  }

  async function download() {
    if (!currentSlide) return;
    setBusy(true);
    setActionError(null);
    try {
      if (sortedSlides.length > 1) {
        await downloadSlidesAsZip(sortedSlides.map(resolveSharedSlideUrl));
      } else {
        // Single-image post: EXACT pre-carousel behavior, untouched.
        if (!imageUrl) return;
        const response = await fetch(imageUrl);
        if (!response.ok) throw new Error("Could not fetch the image.");
        const blob = await response.blob();
        const objectUrl = URL.createObjectURL(blob);
        const anchor = document.createElement("a");
        anchor.href = objectUrl;
        const suffix = current ? current.brand : "original";
        anchor.download = `${post.post_id}_slide${currentSlide.slide_index}_${suffix}_final.png`;
        document.body.appendChild(anchor);
        anchor.click();
        anchor.remove();
        URL.revokeObjectURL(objectUrl);
      }
    } catch (err) {
      setActionError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function copyCaption() {
    try {
      await navigator.clipboard.writeText(post.caption);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setActionError("Clipboard access was blocked by the browser.");
    }
  }

  return (
    <article className="post">
      <div className="post-media">
        <SlideNav count={sortedSlides.length} selectedIndex={selectedSlideIndex} onSelect={setSelectedSlideIndex}>
          {gatedSrc ? (
            <div className={`media-image-wrap ${imageLoading ? "loading" : ""}`}>
              <img src={gatedSrc} alt={`Generated post ${post.post_id}`} loading="lazy" />
              {imageLoading && (
                <div className="media-loading-overlay">
                  <div className="spinner" />
                </div>
              )}
            </div>
          ) : imageUrl ? (
            <div className="media-placeholder">
              <div className="spinner" />
              Loading image...
            </div>
          ) : (
            <div className="media-placeholder">No preview</div>
          )}
        </SlideNav>
      </div>
      <div className="post-body">
        {currentSlide && currentSlide.brands.length > 1 && (
          <BrandToggle
            brands={currentSlide.brands.map((b) => ({ brand: b.brand, status: "completed" as const }))}
            selected={selectedBrand ?? currentSlide.brands[0].brand}
            onSelect={setSelectedBrand}
          />
        )}

        {actionError && <div className="post-err">{actionError}</div>}

        {post.caption && (
          <>
            <span className="caption-label">Caption</span>
            <div className="caption-preview">{post.caption}</div>
          </>
        )}

        <div className="post-actions">
          <button className="btn-primary btn-sm" onClick={download} disabled={busy || !imageUrl}>
            {busy ? "Downloading..." : "Download"}
          </button>
          <button className="btn-secondary btn-sm" onClick={copyCaption} disabled={!post.caption}>
            {copied ? "Copied!" : "Copy caption"}
          </button>
        </div>
      </div>
    </article>
  );
}
