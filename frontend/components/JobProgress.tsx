"use client";

import { computeProgress, formatEta } from "@/lib/eta";
import type { Job, JobPost } from "@/lib/types";

export default function JobProgress({
  job,
  posts,
  live,
}: {
  job: Job;
  posts: JobPost[];
  live: boolean;
}) {
  const progress = computeProgress(posts, job.total_posts || posts.length || job.max_posts);
  const total = Math.max(progress.total, 1);

  const pct = (n: number) => `${(n / total) * 100}%`;
  const working = progress.inFlight;

  const scraping = job.status === "scraping" || job.status === "pending";

  return (
    <div className="card">
      <div className="post-head" style={{ marginBottom: 4 }}>
        <h2 style={{ margin: 0 }}>Progress</h2>
        <span className="post-meta">
          <span className={`live-dot ${live ? "" : "off"}`} />
          {live ? "Live" : "Polling"}
        </span>
      </div>

      {scraping ? (
        <p className="sub" style={{ margin: "10px 0 0" }}>
          {job.input_type === "post"
            ? "Fetching the post from Instagram..."
            : `Scraping up to ${job.max_posts} posts and ranking them by likes...`}{" "}
          This can take a couple of minutes.
        </p>
      ) : (
        <>
          <div className="stats">
            <div className="stat">
              <div className="stat-value">{progress.total}</div>
              <div className="stat-label">Posts found</div>
            </div>
            <div className="stat">
              <div className="stat-value">{working}</div>
              <div className="stat-label">Processing</div>
            </div>
            <div className="stat stat-accent">
              <div className="stat-value">{progress.awaitingUser}</div>
              <div className="stat-label">Waiting on you</div>
            </div>
            <div className="stat stat-ok">
              <div className="stat-value">{progress.completed}</div>
              <div className="stat-label">Completed</div>
            </div>
            {progress.failed > 0 && (
              <div className="stat stat-err">
                <div className="stat-value">{progress.failed}</div>
                <div className="stat-label">Failed</div>
              </div>
            )}
          </div>

          <div className="progress-track">
            <div className="progress-seg seg-done" style={{ width: pct(progress.completed) }} />
            <div className="progress-seg seg-await" style={{ width: pct(progress.awaitingUser) }} />
            <div className="progress-seg seg-work" style={{ width: pct(working) }} />
            <div className="progress-seg seg-fail" style={{ width: pct(progress.failed) }} />
          </div>

          <div className="progress-legend">
            <span>
              <i className="legend-dot" style={{ background: "var(--ok)" }} />
              Completed
            </span>
            <span>
              <i className="legend-dot" style={{ background: "var(--accent)" }} />
              Awaiting your confirmation
            </span>
            <span>
              <i className="legend-dot" style={{ background: "var(--info)" }} />
              Processing
            </span>
            {progress.failed > 0 && (
              <span>
                <i className="legend-dot" style={{ background: "var(--err)" }} />
                Failed
              </span>
            )}
          </div>

          {/*
            Two separate figures on purpose. The countdown covers only work the
            pipeline can do by itself; posts parked on a Confirm click depend on
            the user, so folding them into an ETA would be meaningless.
          */}
          <div className="progress-legend" style={{ marginTop: 14, color: "var(--text-dim)" }}>
            <span>
              <strong style={{ color: "var(--text)" }}>Estimated time remaining:</strong>{" "}
              {working > 0 ? formatEta(progress.etaSeconds) : "nothing running"}
              {working > 0 && !progress.measured && " (initial estimate)"}
            </span>
            {progress.awaitingUser > 0 && (
              <span style={{ color: "var(--accent)" }}>
                {progress.awaitingUser} post{progress.awaitingUser === 1 ? "" : "s"} waiting on your
                confirmation — not counted above
              </span>
            )}
          </div>

          {progress.measured && (
            <div className="hint" style={{ marginTop: 8 }}>
              Measured on this job: ~{Math.round(progress.avgAnalyzeSeconds)}s per analysis, ~
              {Math.round(progress.avgGenerateSeconds)}s per image.
            </div>
          )}
        </>
      )}
    </div>
  );
}
