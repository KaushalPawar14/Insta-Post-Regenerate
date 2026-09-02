"use client";

import { use, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import JobProgress from "@/components/JobProgress";
import PostCard from "@/components/PostCard";
import { authedFetch } from "@/lib/supabase-browser";
import { useJob } from "@/lib/use-job";

export default function JobPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const router = useRouter();
  const { job, posts, loading, live, error, reload } = useJob(id);
  const [busy, setBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);

  const awaiting = useMemo(
    () => posts.filter((p) => p.status === "awaiting_confirmation"),
    [posts]
  );
  const completed = useMemo(() => posts.filter((p) => p.status === "completed"), [posts]);

  // The delete suggestion only appears once every post has been completed AND
  // downloaded. It is a nudge, never an action -- nothing is ever removed on a
  // timer or without an explicit click.
  const allDownloaded =
    posts.length > 0 &&
    completed.length === posts.length &&
    completed.every((p) => p.downloaded);

  async function confirmAll() {
    setBusy("confirm-all");
    setActionError(null);
    try {
      const response = await authedFetch(`/api/jobs/${id}/confirm-all`, { method: "POST" });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.error || "Could not confirm the remaining posts.");
      reload();
    } catch (err) {
      setActionError((err as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function deleteJob() {
    const message = awaiting.length
      ? `Delete this job? ${awaiting.length} post${awaiting.length === 1 ? " is" : "s are"} still awaiting confirmation and will be lost.`
      : "Delete this job and all of its generated images? This cannot be undone.";
    if (!window.confirm(message)) return;

    setBusy("delete");
    setActionError(null);
    try {
      const response = await authedFetch(`/api/jobs/${id}`, { method: "DELETE" });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.error || "Could not delete the job.");
      router.push("/history");
    } catch (err) {
      setActionError((err as Error).message);
      setBusy(null);
    }
  }

  if (loading) {
    return (
      <div className="empty">
        <div className="spinner" style={{ margin: "0 auto 12px" }} />
        Loading job...
      </div>
    );
  }

  if (error || !job) {
    return (
      <>
        <div className="banner banner-err">{error || "Job not found."}</div>
        <Link href="/history">Back to history</Link>
      </>
    );
  }

  return (
    <>
      <div className="post-head" style={{ marginBottom: 6 }}>
        <h1 style={{ margin: 0 }}>
          {job.input_type === "post" ? "Single post" : "Profile job"}
        </h1>
        <span className="post-meta">{new Date(job.created_at).toLocaleString()}</span>
      </div>
      <p className="sub" style={{ wordBreak: "break-all" }}>
        <a href={job.input_url} target="_blank" rel="noopener noreferrer nofollow">
          {job.input_url}
        </a>
      </p>

      {actionError && <div className="banner banner-err">{actionError}</div>}

      {job.status === "failed" && (
        <div className="banner banner-err">
          <div>
            <strong>This job failed.</strong>
            <div style={{ marginTop: 4 }}>{job.error || "No further detail was recorded."}</div>
          </div>
        </div>
      )}

      {allDownloaded && (
        <div className="banner banner-ok">
          <div>
            <strong>Everything here has been downloaded.</strong> You can delete this job to free up
            your storage — entirely up to you, nothing expires on its own.
          </div>
          <div className="banner-actions">
            <button className="btn-danger btn-sm" onClick={deleteJob} disabled={busy !== null}>
              Delete job
            </button>
          </div>
        </div>
      )}

      <JobProgress job={job} posts={posts} live={live} />

      {awaiting.length > 0 && (
        <div className="banner banner-warn" style={{ marginTop: 16 }}>
          <div>
            <strong>
              {awaiting.length} post{awaiting.length === 1 ? "" : "s"} ready for your review.
            </strong>{" "}
            Nothing is generated until you confirm it, so no image-generation spend happens without
            you.
          </div>
          <div className="banner-actions">
            <button className="btn-primary btn-sm" onClick={confirmAll} disabled={busy !== null}>
              {busy === "confirm-all" ? "Queuing..." : `Confirm all ${awaiting.length}`}
            </button>
          </div>
        </div>
      )}

      {posts.length === 0 ? (
        <div className="empty">
          {job.status === "scraping" || job.status === "pending"
            ? "Waiting for the scrape to return posts..."
            : "No posts were produced for this job."}
        </div>
      ) : (
        <div className="post-grid">
          {posts.map((post) => (
            <PostCard key={post.id} post={post} onChanged={reload} />
          ))}
        </div>
      )}

      <div className="btn-row" style={{ marginTop: 28 }}>
        <Link href="/history">
          <button className="btn-ghost btn-sm">Back to history</button>
        </Link>
        <button className="btn-danger btn-sm" onClick={deleteJob} disabled={busy !== null}>
          {busy === "delete" ? "Deleting..." : "Delete this job"}
        </button>
      </div>
    </>
  );
}
