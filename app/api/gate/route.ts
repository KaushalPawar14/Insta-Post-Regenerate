import { NextRequest } from "next/server";
import { GATE_COOKIE, gateEnabled, gateToken } from "@/lib/gate";
import { json } from "@/lib/supabase-admin";

export const runtime = "nodejs";

export async function GET() {
  return json({ enabled: gateEnabled() });
}

export async function POST(request: NextRequest) {
  const password = process.env.SITE_PASSWORD?.trim();
  if (!password) return json({ ok: true, enabled: false });

  let body: { password?: unknown };
  try {
    body = await request.json();
  } catch {
    return json({ error: "Invalid JSON body." }, 400);
  }

  if (typeof body.password !== "string" || body.password !== password) {
    return json({ error: "Incorrect password." }, 401);
  }

  const token = await gateToken(password);
  const response = json({ ok: true });
  response.headers.append(
    "set-cookie",
    [
      `${GATE_COOKIE}=${token}`,
      "Path=/",
      "HttpOnly",
      "SameSite=Lax",
      `Max-Age=${60 * 60 * 24 * 30}`,
      process.env.NODE_ENV === "production" ? "Secure" : "",
    ]
      .filter(Boolean)
      .join("; ")
  );
  return response;
}
