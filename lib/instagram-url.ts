/**
 * Classifies an Instagram URL as a single post/reel or a profile.
 *
 * Both shapes are handed to the same Apify actor (`apify/instagram-scraper`)
 * via `directUrls`; the only difference is that a post URL skips scraping and
 * sorting entirely and yields exactly one item.
 */

export type ParsedInput =
  | { ok: true; inputType: "post"; url: string; shortcode: string }
  | { ok: true; inputType: "profile"; url: string; username: string }
  | { ok: false; error: string };

const POST_PATH = /^\/(p|reel|reels|tv)\/([A-Za-z0-9_-]+)\/?$/;
const PROFILE_PATH = /^\/([A-Za-z0-9._]{1,30})\/?$/;

// Instagram paths that look like usernames but aren't.
const RESERVED = new Set([
  "explore",
  "accounts",
  "directory",
  "developer",
  "about",
  "legal",
  "privacy",
  "terms",
  "stories",
  "direct",
  "reels",
  "p",
  "tv",
  "web",
  "api",
]);

export function parseInstagramInput(raw: string): ParsedInput {
  const trimmed = (raw || "").trim();
  if (!trimmed) return { ok: false, error: "Enter an Instagram profile or post URL." };

  let candidate = trimmed;
  if (!/^https?:\/\//i.test(candidate)) candidate = `https://${candidate}`;

  let parsed: URL;
  try {
    parsed = new URL(candidate);
  } catch {
    return { ok: false, error: "That doesn't look like a valid URL." };
  }

  const host = parsed.hostname.replace(/^www\./i, "").toLowerCase();
  if (host !== "instagram.com" && !host.endsWith(".instagram.com")) {
    return { ok: false, error: "Only instagram.com URLs are supported." };
  }

  // Strip query strings and fragments -- tracking params (igsh, utm_*) would
  // otherwise be passed through to the actor.
  const path = parsed.pathname.replace(/\/{2,}/g, "/");

  const postMatch = path.match(POST_PATH);
  if (postMatch) {
    const kind = postMatch[1] === "reels" ? "reel" : postMatch[1];
    const shortcode = postMatch[2];
    return {
      ok: true,
      inputType: "post",
      url: `https://www.instagram.com/${kind}/${shortcode}/`,
      shortcode,
    };
  }

  const profileMatch = path.match(PROFILE_PATH);
  if (profileMatch) {
    const username = profileMatch[1];
    if (RESERVED.has(username.toLowerCase())) {
      return { ok: false, error: `"/${username}" is not a profile URL.` };
    }
    return {
      ok: true,
      inputType: "profile",
      url: `https://www.instagram.com/${username}/`,
      username,
    };
  }

  return {
    ok: false,
    error:
      "Use a profile URL (instagram.com/username) or a post URL " +
      "(instagram.com/p/SHORTCODE or instagram.com/reel/SHORTCODE).",
  };
}
