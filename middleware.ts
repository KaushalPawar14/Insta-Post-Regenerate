import { NextRequest, NextResponse } from "next/server";
import { GATE_COOKIE, gateToken, safeEqual } from "@/lib/gate";

/**
 * Optional site-wide password gate.
 *
 * Behaviour is driven entirely by the SITE_PASSWORD environment variable:
 *   - UNSET or empty  -> the app is fully open. This is the default.
 *   - set             -> visitors must enter it once; a digest is stored in an
 *                        httpOnly cookie for 30 days.
 *
 * The QStash callback endpoints are never gated -- they are machine-to-machine
 * calls authenticated by QStash's own request signature, and gating them would
 * break the pipeline. They are excluded in the matcher below AND re-checked
 * here, because a routing change must not silently start gating them.
 */
const UNGATED_PREFIXES = [
  "/gate",
  "/api/gate",
  // Python pipeline functions -- authenticated by Upstash-Signature instead.
  "/api/scrape",
  "/api/scrape_poll",
  "/api/analyze",
  "/api/generate",
];

export async function middleware(request: NextRequest) {
  const password = process.env.SITE_PASSWORD?.trim();
  if (!password) return NextResponse.next();

  const { pathname } = request.nextUrl;
  if (UNGATED_PREFIXES.some((prefix) => pathname.startsWith(prefix))) {
    return NextResponse.next();
  }

  const cookie = request.cookies.get(GATE_COOKIE)?.value ?? "";
  const expected = await gateToken(password);
  if (cookie && safeEqual(cookie, expected)) return NextResponse.next();

  if (pathname.startsWith("/api/")) {
    return new NextResponse(JSON.stringify({ error: "This site is password protected." }), {
      status: 401,
      headers: { "content-type": "application/json" },
    });
  }

  const url = request.nextUrl.clone();
  url.pathname = "/gate";
  url.searchParams.set("next", pathname);
  return NextResponse.redirect(url);
}

export const config = {
  matcher: [
    // Everything except Next.js internals, static assets, and the Python
    // pipeline endpoints.
    "/((?!_next/static|_next/image|favicon.ico|api/scrape|api/scrape_poll|api/analyze|api/generate).*)",
  ],
};
