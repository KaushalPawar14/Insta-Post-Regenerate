"use client";

import { useEffect, useRef, useState } from "react";
import { computeProgress, formatEta } from "@/lib/eta";
import { formatInr, usdToInr, usdToInrRate, type Job, type JobPost, type PostStatus } from "@/lib/types";

const STAGE_META: { key: PostStatus; label: string; color: string }[] = [
  { key: "pending", label: "Scraped", color: "var(--text-faint)" },
  { key: "analyzing", label: "Analyzing", color: "var(--info)" },
  { key: "awaiting_confirmation", label: "Awaiting confirmation", color: "var(--accent)" },
  { key: "queued_for_generation", label: "Queued", color: "var(--info)" },
  { key: "generating", label: "Generating", color: "var(--info)" },
  { key: "completed", label: "Completed", color: "var(--ok)" },
  { key: "failed_analysis", label: "Analysis failed", color: "var(--err)" },
  { key: "failed_generation", label: "Generation failed", color: "var(--err)" },
  { key: "removed", label: "Removed", color: "var(--text-faint)" },
];

/** Live-updating count with a small bump animation whenever the value changes. */
function StageChip({ label, color, count }: { label: string; color: string; count: number }) {
  const [bump, setBump] = useState(false);
  const prev = useRef(count);

  useEffect(() => {
    if (prev.current !== count) {
      prev.current = count;
      setBump(true);
      const t = setTimeout(() => setBump(false), 500);
      return () => clearTimeout(t);
    }
  }, [count]);

  return (
    <div className={`stage-chip ${count > 0 ? "has-count" : ""}`}>
      <span className="stage-chip-dot" style={{ background: color, opacity: count > 0 ? 1 : 0.35 }} />
      <div>
        <div className={`stage-chip-count ${bump ? "bump" : ""}`} style={{ color: count > 0 ? "var(--text)" : "var(--text-faint)" }}>
          {count}
        </div>
        <div className="stage-chip-label">{label}</div>
      </div>
    </div>
  );
}

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

  const inrRate = usdToInrRate();
  const totalCostInr = usdToInr(progress.totalCostUsd, inrRate);

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
            {progress.removed > 0 && (
              <div className="stat">
                <div className="stat-value">{progress.removed}</div>
                <div className="stat-label">Removed</div>
              </div>
            )}
          </div>

          <div className="progress-track">
            <div className="progress-seg seg-done" style={{ width: pct(progress.completed) }} />
            <div className="progress-seg seg-await" style={{ width: pct(progress.awaitingUser) }} />
            <div className="progress-seg seg-work" style={{ width: pct(working) }} />
            <div className="progress-seg seg-fail" style={{ width: pct(progress.failed) }} />
          </div>

          {/* Real-time breakdown of exactly how many posts are in each of the
              9 possible stages right now, not just a done/total count -- every
              chip updates live off the same Realtime subscription that feeds
              the posts array, with a small bump animation on change. */}
          <div className="stage-breakdown">
            {STAGE_META.map((s) => (
              <StageChip key={s.key} label={s.label} color={s.color} count={progress.counts[s.key] || 0} />
            ))}
          </div>

          {/*
            Two separate figures on purpose. The countdown covers only work the
            pipeline can do by itself; posts parked on a Confirm click depend on
            the user, so folding them into an ETA would be meaningless.
          */}
          <div className="progress-legend" style={{ marginTop: 4, color: "var(--text-dim)" }}>
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
            <div className="hint" style={{ marginTop: 6 }}>
              Measured on this job: ~{Math.round(progress.avgAnalyzeSeconds)}s per analysis, ~
              {Math.round(progress.avgGenerateSeconds)}s per image.
            </div>
          )}

          {/* Running total cost, visible throughout the job -- updates as each
              post's real vision/image cost lands. OpenAI only: Apify cost is
              deliberately excluded (the user is on Apify's free credits and
              wants it treated as $0 / not applicable, not shown at all --
              see README "Cost tracking"), so no ₹0 Apify line appears here. */}
          <div className="cost-total-card" style={{ marginTop: 14 }}>
            <div>
              <div className="stat-label" style={{ marginBottom: 2 }}>
                Estimated cost so far
              </div>
              <div className="cost-total-value">{formatInr(totalCostInr)}</div>
            </div>
            <div className="hint" style={{ marginLeft: "auto", textAlign: "right" }}>
              ≈ ${progress.totalCostUsd.toFixed(4)} USD · @ ₹{inrRate.toFixed(2)}/$
            </div>
          </div>
        </>
      )}
    </div>
  );
}
