"use client";

import { createClient, type SupabaseClient } from "@supabase/supabase-js";

let client: SupabaseClient | null = null;

export function supabaseBrowser(): SupabaseClient {
  if (client) return client;

  const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
  const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;
  if (!url || !anonKey) {
    throw new Error(
      "Missing NEXT_PUBLIC_SUPABASE_URL / NEXT_PUBLIC_SUPABASE_ANON_KEY. See SETUP.md."
    );
  }

  client = createClient(url, anonKey, {
    auth: {
      persistSession: true,
      autoRefreshToken: true,
      detectSessionInUrl: false,
    },
  });
  return client;
}

/**
 * Every visitor gets a silent anonymous identity on first load. That identity
 * is what RLS scopes their jobs, results and images to -- it is the whole
 * mechanism keeping one visitor's work invisible to another.
 *
 * The session is persisted in localStorage, so returning to the site restores
 * the same identity and therefore the same history. Clearing browser storage
 * loses access to previous jobs (there is no login to recover them with).
 */
export async function ensureAnonymousSession(): Promise<string> {
  const sb = supabaseBrowser();

  const { data: existing } = await sb.auth.getSession();
  if (existing.session?.user?.id) return existing.session.user.id;

  const { data, error } = await sb.auth.signInAnonymously();
  if (error) {
    throw new Error(
      `Could not create an anonymous session: ${error.message}. ` +
        "Make sure Anonymous sign-ins are enabled in Supabase " +
        "(Authentication -> Sign In / Providers)."
    );
  }
  if (!data.session?.user?.id) {
    throw new Error("Supabase returned no anonymous session.");
  }
  return data.session.user.id;
}

/** Bearer token for calls into our own API routes. */
export async function accessToken(): Promise<string> {
  const sb = supabaseBrowser();
  const { data } = await sb.auth.getSession();
  const token = data.session?.access_token;
  if (!token) throw new Error("No active session. Reload the page.");
  return token;
}

export async function authedFetch(input: string, init: RequestInit = {}): Promise<Response> {
  const token = await accessToken();
  return fetch(input, {
    ...init,
    headers: {
      ...(init.headers || {}),
      "content-type": "application/json",
      authorization: `Bearer ${token}`,
    },
  });
}
