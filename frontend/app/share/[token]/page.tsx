"use client";

import { use, useEffect, useState } from "react";

interface SharedPost {
  id: string;
  post_id: string;
  caption: string;
  image_url: string | null;
}

/**
 * Public, read-only view of one job's finished results. No auth, no
 * SessionBoot (see components/SessionBoot.tsx), no Realtime -- it fetches
 * once from the unauthenticated /api/share/[token] route, which is the only
 * thing standing between a visitor and this data. There is nothing here to
 * confirm, remove, or delete; every action is read-only (view, download,
 * copy caption).
 */
export default function SharePage({ params }: { params: Promise<{ token: string }> }) {
  const { token } = use(params);
  const [posts, setPosts] = useState<SharedPost[] | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetch(`/api/share/${token}`)
      .then(async (response) => {
        if (cancelled) return;
        if (response.status === 404) {
          setNotFound(true);
          return;
        }
        if (!response.ok) throw new Error("Could not load this share link.");
        const payload = await response.json();
        setPosts(payload.posts ?? []);
      })
      .catch((err: Error) => {
        if (!cancelled) setError(err.message);
      });
    return () => {
      cancelled = true;
    };
  }, [token]);

  if (notFound) {
    return (
      <div className="empty">This share link doesn&apos;t exist, or has been removed.</div>
    );
  }

  if (error) {
    return <div className="banner banner-err">{error}</div>;
  }

  if (posts === null) {
    return (
      <div className="empty">
        <div className="spinner" style={{ margin: "0 auto 12px" }} />
        Loading shared results...
      </div>
    );
  }

  return (
    <>
      <h1>Shared results</h1>
      <p className="sub">
        {posts.length} generated post{posts.length === 1 ? "" : "s"} -- view, download, or copy the
        caption below.
      </p>

      {posts.length === 0 ? (
        <div className="card">
          <div className="empty">No completed results yet. Check back later.</div>
        </div>
      ) : (
        <div className="post-grid">
          {posts.map((post) => (
            <SharedPostCard key={post.id} post={post} />
          ))}
        </div>
      )}
    </>
  );
}

function SharedPostCard({ post }: { post: SharedPost }) {
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  // Same fetch -> blob -> object URL -> anchor mechanism PostCard.tsx uses,
  // minus the authenticated `.../downloaded` call at the end -- that marks
  // the OWNER's post as downloaded (feeds their private "delete this job?"
  // nudge) and requires a bearer token neither present nor appropriate here.
  async function download() {
    if (!post.image_url) return;
    setBusy(true);
    setActionError(null);
    try {
      const response = await fetch(post.image_url);
      if (!response.ok) throw new Error("Could not fetch the image.");
      const blob = await response.blob();
      const objectUrl = URL.createObjectURL(blob);
      const anchor = document.createElement("a");
      anchor.href = objectUrl;
      anchor.download = `${post.post_id}_final.png`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      URL.revokeObjectURL(objectUrl);
    } catch (err) {
      setActionError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function copyCaption() {
    try {
      await navigator.clipboard.writeText(post.caption);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      setActionError("Clipboard access was blocked by the browser.");
    }
  }

  return (
    <article className="post">
      <div className="post-media">
        {post.image_url ? (
          <img src={post.image_url} alt={`Generated post ${post.post_id}`} loading="lazy" />
        ) : (
          <div className="media-placeholder">No preview</div>
        )}
      </div>
      <div className="post-body">
        {actionError && <div className="post-err">{actionError}</div>}

        {post.caption && (
          <>
            <span className="caption-label">Caption</span>
            <div className="caption-preview">{post.caption}</div>
          </>
        )}

        <div className="post-actions">
          <button className="btn-primary btn-sm" onClick={download} disabled={busy || !post.image_url}>
            {busy ? "Downloading..." : "Download"}
          </button>
          <button className="btn-secondary btn-sm" onClick={copyCaption} disabled={!post.caption}>
            {copied ? "Copied!" : "Copy caption"}
          </button>
        </div>
      </div>
    </article>
  );
}
