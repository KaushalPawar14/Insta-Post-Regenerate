"use client";

import { useEffect, useState } from "react";
import { usePathname } from "next/navigation";
import { ensureAnonymousSession } from "@/lib/supabase-browser";

/**
 * Establishes the visitor's silent anonymous identity before anything renders.
 *
 * Nothing in the (authenticated) app works without it: every row and every
 * stored image is scoped to this identity by Row Level Security, which is
 * what keeps one visitor's jobs and results invisible to everyone else.
 *
 * The public /share/<token> page is the one deliberate exception: it's meant
 * to work for ANY visitor with a link, including one whose browser blocks
 * anonymous auth (third-party storage restrictions, some privacy modes) --
 * it has no need for a session at all, since it only ever calls the
 * unauthenticated /api/share/[token] route. Gating it behind anonymous
 * sign-in would be pure unnecessary coupling (and would mint a throwaway
 * anonymous Supabase user for every random link click), so it skips this
 * entirely rather than being folded into the RLS-scoped identity model.
 */
export default function SessionBoot({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const isPublicShare = pathname?.startsWith("/share/") ?? false;

  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (isPublicShare) return;
    let cancelled = false;
    ensureAnonymousSession()
      .then(() => {
        if (!cancelled) setReady(true);
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, [isPublicShare]);

  if (isPublicShare) return <>{children}</>;

  if (error) {
    return (
      <div className="banner banner-err" role="alert">
        <div>
          <strong>Could not start a session.</strong>
          <div style={{ marginTop: 4 }}>{error}</div>
        </div>
      </div>
    );
  }

  if (!ready) {
    return (
      <div className="empty">
        <div className="spinner" style={{ margin: "0 auto 12px" }} />
        Starting session...
      </div>
    );
  }

  return <>{children}</>;
}
