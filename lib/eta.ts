import type { JobPost, PostStatus } from "./types";

/**
 * Free-tier QStash caps parallelism at 10, so a batch of posts moves through
 * a stage in waves of ten rather than all at once.
 */
const CONCURRENCY = 10;

/** Used until the job has produced real measurements of its own. */
const FALLBACK_ANALYZE_SECONDS = 25;
const FALLBACK_GENERATE_SECONDS = 75;

function durations(posts: JobPost[], startKey: keyof JobPost, endKey: keyof JobPost): number[] {
  const out: number[] = [];
  for (const post of posts) {
    const start = post[startKey] as string | null;
    const end = post[endKey] as string | null;
    if (!start || !end) continue;
    const seconds = (new Date(end).getTime() - new Date(start).getTime()) / 1000;
    if (Number.isFinite(seconds) && seconds > 0 && seconds < 900) out.push(seconds);
  }
  return out;
}

function mean(values: number[], fallback: number): number {
  if (!values.length) return fallback;
  return values.reduce((a, b) => a + b, 0) / values.length;
}

export interface Progress {
  total: number;
  counts: Record<PostStatus, number>;
  /** Posts the pipeline is actively working through. */
  inFlight: number;
  /** Posts parked on the user's Confirm click -- deliberately NOT in the ETA. */
  awaitingUser: number;
  completed: number;
  failed: number;
  /**
   * Seconds of pipeline work left, counting only what can proceed without
   * the user. Null when nothing is in flight.
   */
  etaSeconds: number | null;
  avgAnalyzeSeconds: number;
  avgGenerateSeconds: number;
  measured: boolean;
}

export function computeProgress(posts: JobPost[], targetTotal?: number): Progress {
  const counts = {
    pending: 0,
    analyzing: 0,
    awaiting_confirmation: 0,
    queued_for_generation: 0,
    generating: 0,
    completed: 0,
    failed_analysis: 0,
    failed_generation: 0,
  } as Record<PostStatus, number>;

  for (const post of posts) counts[post.status] = (counts[post.status] ?? 0) + 1;

  const analyzeSamples = durations(posts, "analyze_started_at", "analyze_completed_at");
  const generateSamples = durations(posts, "generate_started_at", "generate_completed_at");

  const avgAnalyzeSeconds = mean(analyzeSamples, FALLBACK_ANALYZE_SECONDS);
  const avgGenerateSeconds = mean(generateSamples, FALLBACK_GENERATE_SECONDS);

  const pendingAnalyze = counts.pending + counts.analyzing;
  const pendingGenerate = counts.queued_for_generation + counts.generating;

  // The two phases are estimated separately because they are gated
  // differently: analysis runs on its own, generation only runs on posts the
  // user has already confirmed. Posts sitting in `awaiting_confirmation` are
  // excluded entirely -- their remaining time depends on the user, not on us,
  // and folding them in would produce a meaningless countdown.
  const analyzeSeconds = Math.ceil(pendingAnalyze / CONCURRENCY) * avgAnalyzeSeconds;
  const generateSeconds = Math.ceil(pendingGenerate / CONCURRENCY) * avgGenerateSeconds;

  const inFlight = pendingAnalyze + pendingGenerate;

  return {
    total: targetTotal ?? posts.length,
    counts,
    inFlight,
    awaitingUser: counts.awaiting_confirmation,
    completed: counts.completed,
    failed: counts.failed_analysis + counts.failed_generation,
    etaSeconds: inFlight > 0 ? Math.round(analyzeSeconds + generateSeconds) : null,
    avgAnalyzeSeconds,
    avgGenerateSeconds,
    measured: analyzeSamples.length > 0 || generateSamples.length > 0,
  };
}

export function formatEta(seconds: number | null): string {
  if (seconds === null) return "--";
  if (seconds < 60) return `~${Math.max(5, Math.round(seconds / 5) * 5)}s`;
  const minutes = Math.floor(seconds / 60);
  const rest = Math.round((seconds % 60) / 15) * 15;
  if (minutes < 60) return rest && rest < 60 ? `~${minutes}m ${rest}s` : `~${minutes}m`;
  const hours = Math.floor(minutes / 60);
  return `~${hours}h ${minutes % 60}m`;
}
