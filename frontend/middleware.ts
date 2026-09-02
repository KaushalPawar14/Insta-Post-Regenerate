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
 * The QStash callback endpoints (/api/scrape, /api/scrape_poll, /api/analyze,
 * /api/generate) never reach this middleware at all: vercel.json's top-level
 * rewrites route them directly to the `backend` Python service before the
 * `frontend` Next.js service (where this middleware runs) ever sees them.
 * They are authenticated by QStash's own request signature instead. The
 * exclusions below are a second, redundant layer of defence -- if a routing
 * change ever let one of these paths reach this service, it still would not
 * get gated, which would break the pipeline.
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
  // Vercel Services does not support the Edge runtime (middleware's default)
  // at all -- deploying with it produces "Edge Runtime is not supported in
  // services." Node.js middleware has been stable since Next.js 15.5; this
  // middleware doesn't use any Edge-only API, so switching runtimes changes
  // nothing about its behavior.
  runtime: "nodejs",
};
