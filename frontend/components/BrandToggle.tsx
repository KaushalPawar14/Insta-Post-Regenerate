"use client";

import { BRAND_LABELS, type Brand, type JobPostBrand } from "@/lib/types";

/**
 * Shared brand tab-toggle, used by BOTH the authenticated PostCard and the
 * public /share/<token> page's read-only card -- per the requirement that
 * the two reuse the same display component rather than separate
 * implementations. This component only renders the tabs and reports which
 * one is selected; each caller wraps it with its own action buttons
 * (download/edit/generate-other-brand for PostCard, download-only for the
 * share page).
 *
 * Self-hides (renders nothing) when fewer than 2 brand rows exist -- a post
 * with only one brand generated shows that image directly, no toggle, same
 * as before multi-brand existed. Callers can render this unconditionally.
 */
export default function BrandToggle({
  brands,
  selected,
  onSelect,
}: {
  brands: Pick<JobPostBrand, "brand" | "status">[];
  selected: Brand;
  onSelect: (brand: Brand) => void;
}) {
  if (brands.length < 2) return null;

  return (
    <div className="brand-toggle" role="tablist" aria-label="Choose format">
      {brands.map((b) => (
        <button
          key={b.brand}
          type="button"
          role="tab"
          aria-selected={selected === b.brand}
          className={`brand-toggle-tab ${selected === b.brand ? "active" : ""} ${
            b.status === "failed_generation" ? "failed" : ""
          }`}
          onClick={() => onSelect(b.brand)}
        >
          {BRAND_LABELS[b.brand]}
          {b.status === "generating" && <span className="brand-toggle-dot generating" />}
          {b.status === "failed_generation" && <span className="brand-toggle-dot failed" />}
        </button>
      ))}
    </div>
  );
}
