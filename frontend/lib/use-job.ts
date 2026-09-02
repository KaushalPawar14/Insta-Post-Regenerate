"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { supabaseBrowser } from "./supabase-browser";
import type { Job, JobPost } from "./types";

export const BUCKET = process.env.NEXT_PUBLIC_SUPABASE_BUCKET || "generated";

/**
 * Live view of one job.
 *
 * Realtime is the primary channel: Postgres change events on `job_posts`,
 * filtered server-side to this job, arrive as each post moves through a stage.
 *
 * A fallback poll runs alongside it because a dropped websocket is silent --
 * without it a visitor could sit watching "analyzing 3/10" forever while the
 * pipeline had actually finished. It polls briskly while the subscription is
 * down or work is in flight, and slowly once things are quiet.
 */
export function useJob(jobId: string) {
  const [job, setJob] = useState<Job | null>(null);
  const [posts, setPosts] = useState<JobPost[]>([]);
  const [loading, setLoading] = useState(true);
  const [live, setLive] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const liveRef = useRef(false);
  const activeRef = useRef(true);

  const load = useCallback(async () => {
    const sb = supabaseBrowser();
    const [jobResult, postsResult] = await Promise.all([
      sb.from("jobs").select("*").eq("id", jobId).maybeSingle(),
      sb.from("job_posts").select("*").eq("job_id", jobId).order("rank", { ascending: true }),
    ]);

    if (jobResult.error) {
      setError(jobResult.error.message);
    } else if (!jobResult.data) {
      setError("Job not found. It may have been deleted.");
      setJob(null);
    } else {
      setError(null);
      setJob(jobResult.data as Job);
    }

    if (!postsResult.error && postsResult.data) {
      setPosts(postsResult.data as JobPost[]);
    }
    setLoading(false);
  }, [jobId]);

  // --- realtime -----------------------------------------------------------
  useEffect(() => {
    void load();

    const sb = supabaseBrowser();
    const channel = sb
      .channel(`job-${jobId}`)
      .on(
        "postgres_changes",
        { event: "*", schema: "public", table: "job_posts", filter: `job_id=eq.${jobId}` },
        (payload) => {
          setPosts((current) => {
            if (payload.eventType === "DELETE") {
              const goneId = (payload.old as { id?: string })?.id;
              return current.filter((p) => p.id !== goneId);
            }
            const incoming = payload.new as JobPost;
            if (!incoming?.id) return current;
            const index = current.findIndex((p) => p.id === incoming.id);
            const next = index === -1 ? [...current, incoming] : current.slice();
            if (index !== -1) next[index] = incoming;
            return next.sort((a, b) => a.rank - b.rank);
          });
        }
      )
      .on(
        "postgres_changes",
        { event: "*", schema: "public", table: "jobs", filter: `id=eq.${jobId}` },
        (payload) => {
          if (payload.eventType === "DELETE") {
            setJob(null);
            setError("This job was deleted.");
            return;
          }
          setJob(payload.new as Job);
        }
      )
      .subscribe((status) => {
        const subscribed = status === "SUBSCRIBED";
        liveRef.current = subscribed;
        setLive(subscribed);
      });

    return () => {
      void sb.removeChannel(channel);
    };
  }, [jobId, load]);

  // --- fallback poll ------------------------------------------------------
  const inFlight = posts.some((p) =>
    ["pending", "analyzing", "queued_for_generation", "generating"].includes(p.status)
  );
  const busy = inFlight || job?.status === "scraping" || job?.status === "pending";
  activeRef.current = busy;

  useEffect(() => {
    let timer: ReturnType<typeof setTimeout>;

    const tick = () => {
      // Brisk while the socket is down or the pipeline is working; relaxed
      // once everything is settled, so an idle tab costs almost nothing.
      const delay = !liveRef.current ? 7000 : activeRef.current ? 15000 : 45000;
      timer = setTimeout(async () => {
        await load();
        tick();
      }, delay);
    };
    tick();

    return () => clearTimeout(timer);
  }, [load]);

  return { job, posts, loading, live, error, reload: load };
}

/** Private-bucket objects are only reachable through a short-lived signed URL. */
export function useSignedUrl(path: string | null | undefined) {
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    if (!path) {
      setUrl(null);
      return;
    }
    let cancelled = false;
    supabaseBrowser()
      .storage.from(BUCKET)
      .createSignedUrl(path, 3600)
      .then(({ data }) => {
        if (!cancelled) setUrl(data?.signedUrl ?? null);
      })
      .catch(() => {
        if (!cancelled) setUrl(null);
      });
    return () => {
      cancelled = true;
    };
  }, [path]);

  return url;
}
