"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { supabaseBrowser } from "./supabase-browser";
import type { Job, JobPost, JobPostBrand, Slide } from "./types";

export const BUCKET = process.env.NEXT_PUBLIC_SUPABASE_BUCKET || "generated";

/**
 * Live view of one job.
 *
 * Realtime is the primary channel: Postgres change events on `job_posts` and
 * `job_post_brands`, filtered server-side to this job, arrive as each post
 * (and each of its brand generations) moves through a stage.
 *
 * A fallback poll runs alongside it because a dropped websocket is silent --
 * without it a visitor could sit watching "analyzing 3/10" forever while the
 * pipeline had actually finished. It polls briskly while the subscription is
 * down or work is in flight, and slowly once things are quiet.
 */
export function useJob(jobId: string) {
  const [job, setJob] = useState<Job | null>(null);
  const [posts, setPosts] = useState<JobPost[]>([]);
  const [brands, setBrands] = useState<JobPostBrand[]>([]);
  const [slides, setSlides] = useState<Slide[]>([]);
  const [loading, setLoading] = useState(true);
  const [live, setLive] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const liveRef = useRef(false);
  const activeRef = useRef(true);

  const load = useCallback(async () => {
    const sb = supabaseBrowser();
    const [jobResult, postsResult, brandsResult, slidesResult] = await Promise.all([
      sb.from("jobs").select("*").eq("id", jobId).maybeSingle(),
      sb.from("job_posts").select("*").eq("job_id", jobId).order("rank", { ascending: true }),
      sb.from("job_post_brands").select("*").eq("job_id", jobId),
      sb.from("post_slides").select("*").eq("job_id", jobId).order("slide_index", { ascending: true }),
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
    if (!brandsResult.error && brandsResult.data) {
      setBrands(brandsResult.data as JobPostBrand[]);
    }
    if (!slidesResult.error && slidesResult.data) {
      setSlides(slidesResult.data as Slide[]);
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
        { event: "*", schema: "public", table: "job_post_brands", filter: `job_id=eq.${jobId}` },
        (payload) => {
          setBrands((current) => {
            if (payload.eventType === "DELETE") {
              const goneId = (payload.old as { id?: string })?.id;
              return current.filter((b) => b.id !== goneId);
            }
            const incoming = payload.new as JobPostBrand;
            if (!incoming?.id) return current;
            const index = current.findIndex((b) => b.id === incoming.id);
            const next = index === -1 ? [...current, incoming] : current.slice();
            if (index !== -1) next[index] = incoming;
            return next;
          });
        }
      )
      .on(
        "postgres_changes",
        { event: "*", schema: "public", table: "post_slides", filter: `job_id=eq.${jobId}` },
        (payload) => {
          setSlides((current) => {
            if (payload.eventType === "DELETE") {
              const goneId = (payload.old as { id?: string })?.id;
              return current.filter((s) => s.id !== goneId);
            }
            const incoming = payload.new as Slide;
            if (!incoming?.id) return current;
            const index = current.findIndex((s) => s.id === incoming.id);
            const next = index === -1 ? [...current, incoming] : current.slice();
            if (index !== -1) next[index] = incoming;
            return next.sort((a, b) => a.slide_index - b.slide_index);
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

  /** This post's brand rows (across ALL of its slides), for PostCard's
   * toggle/generate-other-brand logic and postCostUsd()'s summation. */
  const brandsForPost = useCallback(
    (postId: string) => brands.filter((b) => b.post_id === postId),
    [brands]
  );

  /** This post's slides, in order -- 1 for a plain image post, one per
   * carousel entry otherwise. */
  const slidesForPost = useCallback(
    (postId: string) => slides.filter((s) => s.post_id === postId),
    [slides]
  );

  return { job, posts, brands, slides, brandsForPost, slidesForPost, loading, live, error, reload: load };
}

/** Private-bucket objects are only reachable through a short-lived signed URL. */
export function useSignedUrl(path: string | null | undefined) {
  const [url, setUrl] = useState<string | null>(null);

  useEffect(() => {
    // Reset on EVERY path change, not just when it becomes falsy -- switching
    // from one non-null path straight to another (e.g. toggling brands) must
    // not leave the previous path's signed URL sitting in state, mislabeled
    // as belonging to the new path, while the new one is still being fetched.
    setUrl(null);
    if (!path) return;
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

/**
 * Gates a URL behind the browser actually finishing loading it, so a caller
 * can keep showing the previous image (optionally dimmed) with a spinner
 * overlay instead of letting an <img src=...> swap show a stale frame while
 * the new bytes are still downloading -- the default way browsers handle a
 * src change on an already-rendered <img>.
 *
 * `src` is the last URL that finished loading (stays put, so the old image
 * keeps rendering through the gap); `loading` is true whenever `url` names
 * something newer that hasn't finished loading yet.
 */
export function useGatedImage(url: string | null | undefined) {
  const [src, setSrc] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (!url) {
      setSrc(null);
      setLoading(false);
      return;
    }
    if (url === src) {
      setLoading(false);
      return;
    }
    setLoading(true);
    let cancelled = false;
    const img = new Image();
    img.onload = () => {
      if (cancelled) return;
      setSrc(url);
      setLoading(false);
    };
    img.onerror = () => {
      if (cancelled) return;
      setLoading(false);
    };
    img.src = url;
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- intentionally
    // keyed on `url` alone; `src` is read for its value at effect-run time to
    // skip a redundant reload of an already-displayed image.
  }, [url]);

  return { src, loading };
}
