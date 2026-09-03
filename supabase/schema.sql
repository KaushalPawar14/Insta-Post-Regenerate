-- ===========================================================================
--  Instagram AI Auto-Generator -- Supabase schema
--
--  Run this ONCE in the Supabase dashboard: SQL Editor -> New query -> paste
--  -> Run. It is idempotent, so re-running it is safe.
--
--  What it creates:
--    * jobs / job_posts tables
--    * Row Level Security so one anonymous visitor can never see another's
--      jobs, results, or images
--    * a PRIVATE `generated` Storage bucket, with matching RLS
--    * Realtime publication for live progress updates
-- ===========================================================================

-- ---------------------------------------------------------------- jobs ----
create table if not exists public.jobs (
  id                  uuid primary key default gen_random_uuid(),
  user_id             uuid not null references auth.users (id) on delete cascade,

  input_type          text not null default 'profile'
                        check (input_type in ('profile', 'post')),
  input_url           text not null,

  -- Hard ceiling of 100, enforced at the database layer as a third line of
  -- defence behind the Next.js route and the Python scraper. A forged request
  -- cannot exceed it even if it bypasses the app entirely.
  max_posts           integer not null default 1
                        check (max_posts between 1 and 100),

  status              text not null default 'pending'
                        check (status in ('pending','scraping','analyzing',
                                          'awaiting_confirmation','completed','failed')),

  apify_run_id        text,
  total_posts         integer not null default 0,
  error               text,

  -- Cost tracking (all in USD; converted to INR for display at read time
  -- using a fixed env-var rate, never a live currency API). Real figure from
  -- the Apify run's own usage_total_usd when available; a labeled estimate
  -- otherwise -- see apify_cost_is_estimate and backend/_lib/pricing.py.
  apify_total_cost_usd   numeric not null default 0,
  apify_cost_is_estimate boolean not null default true,

  -- Public "share this job" link. NULL until the owner clicks Share; set
  -- once, then reused. Deliberately NOT the job's own uuid `id` -- a share
  -- link must not double as a way to guess/enumerate real job ids. No RLS
  -- policy grants `anon` access to this table at all; the public /share
  -- page reaches this column only through a server-side route using the
  -- service-role key. See migration_003_share_token.sql.
  share_token         text unique,

  created_at          timestamptz not null default now(),
  updated_at          timestamptz not null default now(),
  scrape_started_at   timestamptz,
  scrape_completed_at timestamptz
);

create index if not exists jobs_user_created_idx
  on public.jobs (user_id, created_at desc);

-- ----------------------------------------------------------- job_posts ----
create table if not exists public.job_posts (
  id                      uuid primary key default gen_random_uuid(),
  job_id                  uuid not null references public.jobs (id) on delete cascade,
  user_id                 uuid not null references auth.users (id) on delete cascade,

  -- Agent 1 output
  post_id                 text not null,
  likes                   integer not null default 0,
  comments                integer not null default 0,
  original_caption        text not null default '',
  raw_image_url           text not null default '',
  rank                    integer not null default 0,

  -- Agent 2 output
  -- `thumb_path` is TRANSIENT: it holds the original scraped thumbnail only so
  -- the user can see what they are confirming. It is deleted from Storage the
  -- moment the generated image exists.
  thumb_path              text,
  image_generation_prompt text not null default '',
  extracted_text          text not null default '',
  refined_caption         text not null default '',

  -- Agent 3 output
  final_image_path        text,

  downloaded              boolean not null default false,

  status                  text not null default 'pending'
                            check (status in ('pending','analyzing','awaiting_confirmation',
                                              'queued_for_generation','generating','completed',
                                              'failed_analysis','failed_generation','removed')),
  error                   text,

  -- Cost tracking, in USD. vision_cost_usd set when analyze.py completes;
  -- image_cost_usd set when generate.py completes; apify_cost_usd is this
  -- post's apportioned share of the job's total scrape cost (see
  -- jobs.apify_total_cost_usd), set for every post as soon as scraping
  -- finishes, before analysis even starts.
  vision_cost_usd         numeric not null default 0,
  image_cost_usd          numeric not null default 0,
  apify_cost_usd          numeric not null default 0,

  -- Timestamps drive the estimated-time-remaining calculation in the UI.
  created_at              timestamptz not null default now(),
  analyze_started_at      timestamptz,
  analyze_completed_at    timestamptz,
  generate_started_at     timestamptz,
  generate_completed_at   timestamptz
);

create index if not exists job_posts_job_idx  on public.job_posts (job_id, rank);
create index if not exists job_posts_user_idx on public.job_posts (user_id);

-- =========================================================================
--  Row Level Security
--
--  Anonymous sign-ins get the `authenticated` role, so these policies cover
--  them. Every policy is scoped to auth.uid(), which is the visitor's
--  anonymous identity -- that is what isolates one visitor from another.
--
--  The Python functions use the service_role key, which bypasses RLS
--  entirely; they set user_id explicitly on every write.
-- =========================================================================
alter table public.jobs      enable row level security;
alter table public.job_posts enable row level security;

drop policy if exists "own jobs: select" on public.jobs;
drop policy if exists "own jobs: insert" on public.jobs;
drop policy if exists "own jobs: update" on public.jobs;
drop policy if exists "own jobs: delete" on public.jobs;

create policy "own jobs: select" on public.jobs
  for select to authenticated using (user_id = (select auth.uid()));
create policy "own jobs: insert" on public.jobs
  for insert to authenticated with check (user_id = (select auth.uid()));
create policy "own jobs: update" on public.jobs
  for update to authenticated
  using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));
create policy "own jobs: delete" on public.jobs
  for delete to authenticated using (user_id = (select auth.uid()));

drop policy if exists "own posts: select" on public.job_posts;
drop policy if exists "own posts: insert" on public.job_posts;
drop policy if exists "own posts: update" on public.job_posts;
drop policy if exists "own posts: delete" on public.job_posts;

create policy "own posts: select" on public.job_posts
  for select to authenticated using (user_id = (select auth.uid()));
create policy "own posts: insert" on public.job_posts
  for insert to authenticated with check (user_id = (select auth.uid()));
create policy "own posts: update" on public.job_posts
  for update to authenticated
  using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));
create policy "own posts: delete" on public.job_posts
  for delete to authenticated using (user_id = (select auth.uid()));

-- =========================================================================
--  Realtime
--
--  REPLICA IDENTITY FULL makes the whole row available to Realtime, which is
--  what lets Supabase evaluate the RLS policy above against each change
--  before deciding whether to deliver it to a subscriber.
-- =========================================================================
alter table public.jobs      replica identity full;
alter table public.job_posts replica identity full;

do $$
begin
  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = 'jobs'
  ) then
    alter publication supabase_realtime add table public.jobs;
  end if;

  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = 'job_posts'
  ) then
    alter publication supabase_realtime add table public.job_posts;
  end if;
end $$;

-- =========================================================================
--  Storage
--
--  PRIVATE bucket. Object paths are `<user_id>/<job_id>/<filename>`, and the
--  policies below match the first path segment against auth.uid() -- so a
--  guessed path from another visitor still returns nothing.
-- =========================================================================
insert into storage.buckets (id, name, public)
values ('generated', 'generated', false)
on conflict (id) do nothing;

drop policy if exists "own files: select" on storage.objects;
drop policy if exists "own files: insert" on storage.objects;
drop policy if exists "own files: update" on storage.objects;
drop policy if exists "own files: delete" on storage.objects;

create policy "own files: select" on storage.objects
  for select to authenticated
  using (bucket_id = 'generated'
         and (storage.foldername(name))[1] = (select auth.uid())::text);

create policy "own files: insert" on storage.objects
  for insert to authenticated
  with check (bucket_id = 'generated'
              and (storage.foldername(name))[1] = (select auth.uid())::text);

create policy "own files: update" on storage.objects
  for update to authenticated
  using (bucket_id = 'generated'
         and (storage.foldername(name))[1] = (select auth.uid())::text);

create policy "own files: delete" on storage.objects
  for delete to authenticated
  using (bucket_id = 'generated'
         and (storage.foldername(name))[1] = (select auth.uid())::text);
