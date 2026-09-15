"use client";

/**
 * ZIP download for multi-slide (carousel) posts only. Single-image posts
 * never call this -- their Download button keeps its exact pre-carousel
 * single-file behavior untouched (see PostCard.tsx / share/[token]/page.tsx,
 * both of which branch on slide count BEFORE ever reaching this file).
 *
 * Browsers cannot hand back an actual OS folder from a download, so a ZIP is
 * the standard equivalent -- extracting it produces one. jszip is loaded
 * dynamically so it never adds to the bundle single-image posts (the common
 * case) actually load.
 */

const ZIP_COUNTER_KEY = "ig_carousel_zip_counter";
let fallbackCounter = 0;

/**
 * Increments per ZIP download in THIS browser -- e.g. slide_1.zip, then
 * slide_2.zip. Deliberately not server-tracked or globally consistent (the
 * spec only asks for a simple per-browser-session counter). localStorage
 * persists it across page loads within the browser; if storage is
 * unavailable (private mode, etc.) this falls back to a counter that only
 * survives the current page load, which is still a reasonable degradation.
 */
function nextZipCounter(): number {
  try {
    const raw = window.localStorage.getItem(ZIP_COUNTER_KEY);
    const next = (raw ? parseInt(raw, 10) : 0) + 1;
    window.localStorage.setItem(ZIP_COUNTER_KEY, String(next));
    return next;
  } catch {
    fallbackCounter += 1;
    return fallbackCounter;
  }
}

function extensionForBlob(blob: Blob): string {
  if (blob.type === "image/jpeg" || blob.type === "image/jpg") return "jpg";
  if (blob.type === "image/webp") return "webp";
  // Every image this app produces or stores is PNG (generated) or JPEG
  // (original/thumbnail) -- PNG is the safe default for anything else.
  return "png";
}

function saveBlob(blob: Blob, filename: string) {
  const objectUrl = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = objectUrl;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(objectUrl);
}

/**
 * Fetch each URL (already resolved by the caller to whichever image is
 * currently showing for that slide -- generated or original) and bundle
 * them into one ZIP, named `1.<ext>`, `2.<ext>`, ... in the exact order
 * given. `null` entries (a slide with nothing ready to download yet) are
 * skipped WITHOUT leaving a gap in the numbering -- the sequence only
 * advances for files actually added. No caption or other text file is ever
 * added, only the images.
 */
export async function downloadSlidesAsZip(urls: (string | null)[]): Promise<void> {
  const { default: JSZip } = await import("jszip");
  const zip = new JSZip();
  let sequence = 0;

  for (const url of urls) {
    if (!url) continue;
    const response = await fetch(url);
    if (!response.ok) continue;
    const blob = await response.blob();
    sequence += 1;
    zip.file(`${sequence}.${extensionForBlob(blob)}`, blob);
  }

  if (sequence === 0) {
    throw new Error("No images are ready to download yet.");
  }

  const zipBlob = await zip.generateAsync({ type: "blob" });
  saveBlob(zipBlob, `slide_${nextZipCounter()}.zip`);
}
