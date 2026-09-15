"use client";

/**
 * Left/right arrow navigation across a post's slides -- shared by stage 2
 * (raw scraped image + checkbox), the completed-result view (generated-or-
 * original image + BrandToggle), and reused verbatim across the results
 * page, History (same PostCard), and the public /share/<token> page, per
 * the requirement that all three reuse the same component.
 *
 * Self-hides the arrows/count (renders only `children`) when there's fewer
 * than 2 slides -- same convention as BrandToggle's own `< 2` self-hide --
 * so a plain image post's rendering is pixel-identical to before this
 * feature existed. `children` is whatever the caller has already computed
 * for the CURRENTLY SELECTED slide; this component only owns the arrows and
 * the index, not the slide content itself (which needs hooks -- useSignedUrl,
 * useGatedImage -- that must be called from the caller's own top level, not
 * from inside a render-prop here).
 */
export default function SlideNav({
  count,
  selectedIndex,
  onSelect,
  children,
}: {
  count: number;
  selectedIndex: number;
  onSelect: (index: number) => void;
  children: React.ReactNode;
}) {
  if (count === 0) return null;

  return (
    <div className="slide-nav">
      {children}
      {count > 1 && (
        <>
          <button
            type="button"
            className="slide-nav-arrow slide-nav-arrow-left"
            aria-label="Previous slide"
            onClick={() => onSelect((selectedIndex - 1 + count) % count)}
          >
            ‹
          </button>
          <button
            type="button"
            className="slide-nav-arrow slide-nav-arrow-right"
            aria-label="Next slide"
            onClick={() => onSelect((selectedIndex + 1) % count)}
          >
            ›
          </button>
          <div className="slide-nav-count">
            {selectedIndex + 1} / {count}
          </div>
        </>
      )}
    </div>
  );
}
