"use client";

import { useState } from "react";
import { authedFetch } from "@/lib/supabase-browser";
import type { Slide } from "@/lib/types";

/**
 * Replaces the old per-slide checkbox + arrow-navigation review step
 * entirely. Renders PURELY from the `slides` already loaded into the job
 * page's state (via useJob) -- no image loading, no navigation, no
 * per-slide fetch, so it opens instantly even for a post with many slides.
 * Just numbers and checkboxes.
 *
 * Local draft state only: checking/unchecking boxes here does nothing to
 * the server until Confirm, which sends the whole selection in one batch
 * call (PATCH /api/posts/[id]/slide-selection). Closing without confirming
 * discards the draft -- the server-side selection is unchanged.
 */
export default function SlideSelectDialog({
  postId,
  slides,
  onClose,
  onSaved,
}: {
  postId: string;
  slides: Slide[];
  onClose: () => void;
  onSaved: () => void;
}) {
  const [checked, setChecked] = useState<Set<number>>(
    () => new Set(slides.filter((s) => s.include_in_analysis).map((s) => s.slide_index))
  );
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function toggle(index: number) {
    setChecked((current) => {
      const next = new Set(current);
      if (next.has(index)) next.delete(index);
      else next.add(index);
      return next;
    });
  }

  async function confirm() {
    setSaving(true);
    setError(null);
    try {
      const response = await authedFetch(`/api/posts/${postId}/slide-selection`, {
        method: "PATCH",
        body: JSON.stringify({ selected_indices: Array.from(checked) }),
      });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.error || "Could not save your selection.");
      onSaved();
      onClose();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="dialog-overlay" onClick={saving ? undefined : onClose}>
      <div className="dialog-box" onClick={(e) => e.stopPropagation()}>
        <h3 style={{ marginTop: 0 }}>Select slides to generate</h3>
        <p className="sub" style={{ marginTop: 0 }}>
          Check the slides you want analyzed and generated. Unchecked slides cost nothing and keep
          their original image in the result.
        </p>

        <div className="slide-select-grid">
          {slides.map((slide) => (
            <label key={slide.id} className="slide-select-item">
              <input
                type="checkbox"
                checked={checked.has(slide.slide_index)}
                onChange={() => toggle(slide.slide_index)}
                disabled={saving}
              />
              {slide.slide_index + 1}
            </label>
          ))}
        </div>

        {error && <div className="post-err">{error}</div>}

        <div className="post-actions" style={{ marginTop: 16 }}>
          <button className="btn-primary btn-sm" onClick={confirm} disabled={saving}>
            {saving ? "Saving..." : `Confirm (${checked.size} selected)`}
          </button>
          <button className="btn-ghost btn-sm" onClick={onClose} disabled={saving}>
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
