-- ===========================================================================
--  Migration 004: multi-brand image generation (Facts4Genius + Facts Bytes)
--
--  Run this ONCE in the Supabase dashboard: SQL Editor -> New query -> paste
--  -> Run. Idempotent -- safe to re-run (the backfill INSERT is guarded by
--  `where not exists`, so re-running never creates duplicate rows).
--
--  supabase/schema.sql has also been updated to include this table for a
--  brand-new install; this migration is for the already-deployed database.
--
--  WHY A NEW TABLE, NOT PARALLEL COLUMNS ON job_posts:
--  A post can now have 0, 1, or 2 independent generations (one per brand),
--  each independently claimable, retryable, and costed. That is exactly a
--  one-row-per-(post,brand) relationship -- the same pattern job_posts
--  itself already uses relative to jobs. Parallel columns
--  (facts4genius_status, factsbytes_status, ...) would double every
--  generation-related column and turn "for each selected brand" logic
--  (fan-out, retry-one-brand, sum-two-costs) into conditionals instead of a
--  loop over rows.
--
--  job_posts.status KEEPS ITS EXACT EXISTING ENUM AND MEANING. It is no
--  longer set directly by the generate step; instead it's a rollup computed
--  from this table's rows for that post (see backend/_lib/pipeline.py's
--  refresh_post_status): any row generating -> generating; all rows still
--  queued -> queued_for_generation; all rows terminal with >=1 completed ->
--  completed; all rows terminal with none completed -> failed_generation.
--  This is why PostStepper, the stage-breakdown UI, and the ETA calculation
--  needed ZERO changes -- they still just read job_posts.status.
-- ===========================================================================

create table if not exists public.job_post_brands (
  id                     uuid primary key default gen_random_uuid(),
  post_id                uuid not null references public.job_posts (id) on delete cascade,

  -- Denormalized (rather than joined through job_posts every time): job_id
  -- for the Realtime filter (same filter-by-job_id pattern jobs/job_posts
  -- already use), user_id for RLS -- both mirror how job_posts itself
  -- denormalizes user_id from jobs rather than requiring a join.
  job_id                 uuid not null references public.jobs (id) on delete cascade,
  user_id                uuid not null references auth.users (id) on delete cascade,

  brand                  text not null check (brand in ('facts4genius', 'factsbytes')),

  status                 text not null default 'queued_for_generation'
                           check (status in ('queued_for_generation', 'generating',
                                             'completed', 'failed_generation')),

  final_image_path       text,
  image_cost_usd         numeric not null default 0,
  error                  text,

  generate_started_at    timestamptz,
  generate_completed_at  timestamptz,
  created_at             timestamptz not null default now(),

  -- At most one row per (post, brand) -- this is what makes "does the other
  -- brand already have a row" a simple existence check, and what the atomic
  -- claim (UPDATE ... WHERE post_id = ? AND brand = ? AND status = ?) relies
  -- on to be unambiguous.
  unique (post_id, brand)
);

create index if not exists job_post_brands_post_idx on public.job_post_brands (post_id);
create index if not exists job_post_brands_job_idx  on public.job_post_brands (job_id);
create index if not exists job_post_brands_user_idx on public.job_post_brands (user_id);

-- --------------------------------------------------------------- backfill --
-- Every post that completed BEFORE this migration has its single image on
-- the legacy job_posts.final_image_path/image_cost_usd columns, with no
-- job_post_brands row at all. Backfill one 'facts4genius' row per such post
-- so the application code can uniformly read job_post_brands going forward
-- with no legacy special-casing. The legacy columns are left in place,
-- unused, afterward -- same "leave in place, don't delete" convention as
-- the earlier Apify-cost change.
insert into public.job_post_brands
  (post_id, job_id, user_id, brand, status, final_image_path, image_cost_usd, generate_completed_at)
select
  jp.id, jp.job_id, jp.user_id, 'facts4genius', 'completed',
  jp.final_image_path, jp.image_cost_usd, jp.generate_completed_at
from public.job_posts jp
where jp.final_image_path is not null
  and not exists (
    select 1 from public.job_post_brands b
    where b.post_id = jp.id and b.brand = 'facts4genius'
  );

-- =========================================================================
--  Row Level Security -- exact same shape as job_posts' own policies.
-- =========================================================================
alter table public.job_post_brands enable row level security;

drop policy if exists "own post brands: select" on public.job_post_brands;
drop policy if exists "own post brands: insert" on public.job_post_brands;
drop policy if exists "own post brands: update" on public.job_post_brands;
drop policy if exists "own post brands: delete" on public.job_post_brands;

create policy "own post brands: select" on public.job_post_brands
  for select to authenticated using (user_id = (select auth.uid()));
create policy "own post brands: insert" on public.job_post_brands
  for insert to authenticated with check (user_id = (select auth.uid()));
create policy "own post brands: update" on public.job_post_brands
  for update to authenticated
  using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));
create policy "own post brands: delete" on public.job_post_brands
  for delete to authenticated using (user_id = (select auth.uid()));

-- =========================================================================
--  Realtime -- same REPLICA IDENTITY FULL + publication pattern as
--  jobs/job_posts, so PostCard's brand toggle updates live.
-- =========================================================================
alter table public.job_post_brands replica identity full;

do $$
begin
  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = 'job_post_brands'
  ) then
    alter publication supabase_realtime add table public.job_post_brands;
  end if;
end $$;
