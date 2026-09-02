"use client";

import { useEffect, useState } from "react";
import { ensureAnonymousSession } from "@/lib/supabase-browser";

/**
 * Establishes the visitor's silent anonymous identity before anything renders.
 *
 * Nothing in the app works without it: every row and every stored image is
 * scoped to this identity by Row Level Security, which is what keeps one
 * visitor's jobs and results invisible to everyone else.
 */
export default function SessionBoot({ children }: { children: React.ReactNode }) {
  const [ready, setReady] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
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
  }, []);

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
