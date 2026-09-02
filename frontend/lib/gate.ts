export const GATE_COOKIE = "ig_gate";

/**
 * Derives the cookie value from SITE_PASSWORD.
 *
 * The password itself is never written to the cookie -- only this digest --
 * so reading a visitor's cookie does not reveal it. Uses Web Crypto so the
 * same helper works in both the Edge middleware and Node route handlers.
 */
export async function gateToken(password: string): Promise<string> {
  const data = new TextEncoder().encode(`ig-gate:v1:${password}`);
  const digest = await crypto.subtle.digest("SHA-256", data);
  return Array.from(new Uint8Array(digest))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}

/** Length-independent comparison, to avoid leaking the digest via timing. */
export function safeEqual(a: string, b: string): boolean {
  if (a.length !== b.length) return false;
  let diff = 0;
  for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
  return diff === 0;
}

/** The gate only exists when SITE_PASSWORD is set. Unset means fully open. */
export function gateEnabled(): boolean {
  return Boolean(process.env.SITE_PASSWORD && process.env.SITE_PASSWORD.trim());
}
