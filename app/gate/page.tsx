"use client";

import { useState } from "react";

export default function GatePage() {
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const response = await fetch("/api/gate", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ password }),
      });
      if (!response.ok) {
        const payload = await response.json().catch(() => ({}));
        throw new Error(payload.error || "Incorrect password.");
      }
      // Full reload so the middleware re-evaluates with the new cookie.
      const next = new URLSearchParams(window.location.search).get("next") || "/";
      window.location.href = next;
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  return (
    <div className="center-narrow">
      <h1>Password required</h1>
      <p className="sub">Enter the access password to continue. You&apos;ll only be asked once.</p>

      {error && (
        <div className="banner banner-err" role="alert">
          {error}
        </div>
      )}

      <form className="card" onSubmit={submit}>
        <div className="field">
          <label htmlFor="pw">Password</label>
          <input
            id="pw"
            type="password"
            value={password}
            autoFocus
            onChange={(e) => setPassword(e.target.value)}
            disabled={busy}
          />
        </div>
        <div className="btn-row" style={{ marginTop: 18 }}>
          <button type="submit" className="btn-primary" disabled={busy || !password}>
            {busy ? "Checking..." : "Continue"}
          </button>
        </div>
      </form>
    </div>
  );
}
