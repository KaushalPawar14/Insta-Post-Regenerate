"use client";

import { useCallback, useEffect, useState } from "react";
import Link from "next/link";
import { authedFetch, supabaseBrowser } from "@/lib/supabase-browser";
import { createShareLink } from "@/lib/share";
import { BRAND_LABELS, type Brand, type Job, type JobPost } from "@/lib/types";

interface JobSummary extends Job {
  counts: { total: number; awaiting: number; completed: number; failed: number };
  /** Brand(s) with at least one completed job_post_brands row anywhere in
   * this job -- a job-level aggregate, not per-post detail (that lives on
   * the post cards once the job is opened). Empty until something has
   * actually finished generating. */
  completedBrands: Brand[];
}

const STATUS_TEXT: Record<Job["status"], string> = {
  pending: "Queued",
  scraping: "Scraping",
  analyzing: "Analyzing",
  awaiting_confirmation: "Awaiting your confirmation",
  completed: "Completed",
  failed: "Failed",
};

export default function HistoryPage() {
  const [jobs, setJobs] = useState<JobSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [copiedJobId, setCopiedJobId] = useState<string | null>(null);

  const load = useCallback(async () => {
    const sb = supabaseBrowser();

    // RLS scopes both queries to this visitor's anonymous identity, so no
    // user filter is needed here -- and none can be bypassed.
    const { data: jobRows, error: jobError } = await sb
      .from("jobs")
      .select("*")
      .order("created_at", { ascending: false });

    if (jobError) {
      setError(jobError.message);
      setLoading(false);
      return;
    }

    const ids = (jobRows ?? []).map((j) => j.id);
    let postRows: Pick<JobPost, "job_id" | "status">[] = [];
    let brandRows: { job_id: string; brand: Brand }[] = [];
    if (ids.length) {
      const [postsResult, brandsResult] = await Promise.all([
        sb.from("job_posts").select("job_id, status").in("job_id", ids),
        sb.from("job_post_brands").select("job_id, brand").in("job_id", ids).eq("status", "completed"),
      ]);
      postRows = (postsResult.data ?? []) as Pick<JobPost, "job_id" | "status">[];
      brandRows = (brandsResult.data ?? []) as { job_id: string; brand: Brand }[];
    }

    setJobs(
      (jobRows ?? []).map((job) => {
        const mine = postRows.filter((p) => p.job_id === job.id);
        const myBrands = new Set(brandRows.filter((b) => b.job_id === job.id).map((b) => b.brand));
        return {
          ...(job as Job),
          counts: {
            total: mine.length,
            awaiting: mine.filter((p) => p.status === "awaiting_confirmation").length,
            completed: mine.filter((p) => p.status === "completed").length,
            failed: mine.filter((p) => p.status.startsWith("failed")).length,
          },
          completedBrands: Array.from(myBrands),
        };
      })
    );
    setError(null);
    setLoading(false);
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  async function share(jobId: string) {
    setBusy(jobId);
    try {
      const url = await createShareLink(jobId);
      await navigator.clipboard.writeText(url);
      setCopiedJobId(jobId);
      setTimeout(() => setCopiedJobId((current) => (current === jobId ? null : current)), 2000);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function remove(jobId: string) {
    if (!window.confirm("Delete this job and all of its generated images? This cannot be undone."))
      return;
    setBusy(jobId);
    try {
      const response = await authedFetch(`/api/jobs/${jobId}`, { method: "DELETE" });
      const payload = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(payload.error || "Could not delete the job.");
      await load();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(null);
    }
  }

  if (loading) {
    return (
      <div className="empty">
        <div className="spinner" style={{ margin: "0 auto 12px" }} />
        Loading your jobs...
      </div>
    );
  }

  return (
    <>
      <h1>Your jobs</h1>
      <p className="sub">
        Everything you&apos;ve run, including posts still waiting on your confirmation. Nothing here
        expires — jobs stay until you delete them.
      </p>

      {error && <div className="banner banner-err">{error}</div>}

      {jobs.length === 0 ? (
        <div className="card">
          <div className="empty">
            You haven&apos;t run anything yet.
            <div style={{ marginTop: 14 }}>
              <Link href="/">
                <button className="btn-primary btn-sm">Start a job</button>
              </Link>
            </div>
          </div>
        </div>
      ) : (
        <div className="card">
          {jobs.map((job) => (
            <div className="job-row" key={job.id}>
              <div className="job-row-main">
                <Link href={`/job/${job.id}`} className="job-url">
                  {job.input_url}
                </Link>
                {job.completedBrands.length > 0 && (
                  <span className="brand-tags">
                    {job.completedBrands.map((b) => (
                      <span className="brand-tag" key={b}>
                        {BRAND_LABELS[b]}
                      </span>
                    ))}
                  </span>
                )}
                <div className="job-sub">
                  {new Date(job.created_at).toLocaleString()} ·{" "}
                  {job.input_type === "post" ? "single post" : `up to ${job.max_posts} posts`} ·{" "}
                  {STATUS_TEXT[job.status]}
                  {job.counts.total > 0 && (
                    <>
                      {" · "}
                      {job.counts.completed}/{job.counts.total} completed
                      {job.counts.awaiting > 0 && (
                        <span style={{ color: "var(--accent)" }}>
                          {" · "}
                          {job.counts.awaiting} awaiting you
                        </span>
                      )}
                      {job.counts.failed > 0 && (
                        <span style={{ color: "var(--err)" }}>
                          {" · "}
                          {job.counts.failed} failed
                        </span>
                      )}
                    </>
                  )}
                </div>
              </div>
              <div className="btn-row">
                <Link href={`/job/${job.id}`}>
                  <button className="btn-secondary btn-sm">Open</button>
                </Link>
                <button
                  className="btn-secondary btn-sm"
                  onClick={() => share(job.id)}
                  disabled={busy !== null}
                >
                  {busy === job.id && copiedJobId !== job.id
                    ? "Sharing..."
                    : copiedJobId === job.id
                      ? "Link copied!"
                      : "Share"}
                </button>
                <button
                  className="btn-danger btn-sm"
                  onClick={() => remove(job.id)}
                  disabled={busy !== null}
                >
                  {busy === job.id ? "Deleting..." : "Delete"}
                </button>
              </div>
            </div>
          ))}
        </div>
      )}
    </>
  );
}
