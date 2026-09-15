-- ===========================================================================
--  Migration 006: carousel/sidecar posts (post_slides)
--
--  Run this ONCE in the Supabase dashboard: SQL Editor -> New query -> paste
--  -> Run. Idempotent -- safe to re-run.
--
--  supabase/schema.sql has also been updated with the final shape (the
--  post_slides table, job_post_brands.slide_id, job_posts.slide_count, the
--  new awaiting_slide_selection status, and job_posts.image_generation_prompt
--  / extracted_text removed), so a BRAND NEW project can just run schema.sql
--  alone and skip this file. This migration exists for the already-deployed
--  database, where schema.sql's `create table if not exists` is a no-op
--  against existing tables and cannot add/drop columns or tighten checks.
--
--  See README "Carousel / multi-slide posts" for the full schema rationale:
--  a separate post_slides table (not parallel columns) because a post's
--  slide count is no longer fixed at one, and job_post_brands is re-keyed to
--  slide_id (while keeping post_id denormalized) so every existing
--  post-level rollup/aggregation query keeps working unmodified across a
--  post with more than one slide.
-- ===========================================================================

-- ----------------------------------------------------------- job_posts ----
alter table public.job_posts
  add column if not exists slide_count integer not null default 1;

-- Add 'awaiting_slide_selection' to job_posts.status's allowed values.
-- Postgres has no "ADD VALUE IF NOT EXISTS" for a plain CHECK constraint, so
-- this drops and recreates it -- safe, since dropping a CHECK constraint
-- doesn't touch any data, and the recreated one is a strict superset of the
-- old one (every value the old constraint allowed is still allowed here).
-- The constraint's name is looked up dynamically (same pattern as
-- migration_002/005) rather than assumed, so this works whatever it's
-- actually called.
do $$
declare
  existing_constraint_name text;
begin
  select con.conname into existing_constraint_name
  from pg_constraint con
  join pg_class rel      on rel.oid = con.conrelid
  join pg_namespace nsp  on nsp.oid = rel.relnamespace
  where con.contype = 'c'
    and nsp.nspname = 'public'
    and rel.relname = 'job_posts'
    and pg_get_constraintdef(con.oid) ilike '%status%'
    and pg_get_constraintdef(con.oid) ilike '%pending%'
  limit 1;

  if existing_constraint_name is not null then
    execute format('alter table public.job_posts drop constraint %I', existing_constraint_name);
  end if;
end $$;

alter table public.job_posts drop constraint if exists job_posts_status_check;
alter table public.job_posts
  add constraint job_posts_status_check
  check (status in ('pending','awaiting_slide_selection','analyzing',
                    'awaiting_confirmation','queued_for_generation','generating',
                    'completed','failed_analysis','failed_generation','removed'));

-- ------------------------------------------------------------ post_slides --
create table if not exists public.post_slides (
  id                      uuid primary key default gen_random_uuid(),
  post_id                 uuid not null references public.job_posts (id) on delete cascade,
  job_id                  uuid not null references public.jobs (id) on delete cascade,
  user_id                 uuid not null references auth.users (id) on delete cascade,

  slide_index             integer not null,
  raw_image_url           text not null default '',
  thumb_path              text,
  include_in_analysis     boolean not null default true,

  status                  text not null default 'pending'
                            check (status in ('pending','analyzing','analyzed',
                                              'failed_analysis','skipped')),

  image_generation_prompt text not null default '',
  extracted_text          text not null default '',
  refined_caption         text not null default '',
  vision_cost_usd         numeric not null default 0,
  error                   text,

  analyze_started_at      timestamptz,
  analyze_completed_at    timestamptz,
  created_at              timestamptz not null default now(),

  unique (post_id, slide_index)
);

create index if not exists post_slides_post_idx on public.post_slides (post_id, slide_index);
create index if not exists post_slides_job_idx  on public.post_slides (job_id);
create index if not exists post_slides_user_idx on public.post_slides (user_id);

alter table public.post_slides enable row level security;

drop policy if exists "own slides: select" on public.post_slides;
drop policy if exists "own slides: insert" on public.post_slides;
drop policy if exists "own slides: update" on public.post_slides;
drop policy if exists "own slides: delete" on public.post_slides;

create policy "own slides: select" on public.post_slides
  for select to authenticated using (user_id = (select auth.uid()));
create policy "own slides: insert" on public.post_slides
  for insert to authenticated with check (user_id = (select auth.uid()));
create policy "own slides: update" on public.post_slides
  for update to authenticated
  using (user_id = (select auth.uid())) with check (user_id = (select auth.uid()));
create policy "own slides: delete" on public.post_slides
  for delete to authenticated using (user_id = (select auth.uid()));

alter table public.post_slides replica identity full;

do $$
begin
  if not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime' and schemaname = 'public' and tablename = 'post_slides'
  ) then
    alter publication supabase_realtime add table public.post_slides;
  end if;
end $$;

-- --------------------------------------------------- backfill: one slide --
-- Every existing job_posts row becomes exactly one post_slides row
-- (slide_index 0), carrying its existing per-image analyzer output across.
-- `include_in_analysis` is true and `status` is derived from whatever the
-- post's own analysis already produced, so an already-completed post's
-- backfilled slide is itself already terminal (analyzed/failed_analysis) --
-- no re-analysis is ever triggered by this backfill.
insert into public.post_slides (
  post_id, job_id, user_id, slide_index, raw_image_url, thumb_path,
  include_in_analysis, status, image_generation_prompt, extracted_text,
  refined_caption, vision_cost_usd, error, analyze_started_at, analyze_completed_at
)
select
  jp.id, jp.job_id, jp.user_id, 0, jp.raw_image_url, jp.thumb_path,
  true,
  case
    when jp.status in ('pending') then 'pending'
    when jp.status in ('analyzing') then 'analyzing'
    when jp.status in ('awaiting_confirmation','queued_for_generation','generating',
                        'completed','failed_generation') then 'analyzed'
    when jp.status = 'failed_analysis' then 'failed_analysis'
    else 'pending'
  end,
  coalesce(jp.image_generation_prompt, ''),
  coalesce(jp.extracted_text, ''),
  coalesce(jp.refined_caption, ''),
  coalesce(jp.vision_cost_usd, 0),
  jp.error,
  jp.analyze_started_at,
  jp.analyze_completed_at
from public.job_posts jp
where not exists (
  select 1 from public.post_slides ps where ps.post_id = jp.id and ps.slide_index = 0
);

-- job_posts.image_generation_prompt / extracted_text are dropped below (now
-- live exclusively on post_slides), so nothing reads them from job_posts
-- going forward -- drop only after the backfill above has copied them.
alter table public.job_posts drop column if exists image_generation_prompt;
alter table public.job_posts drop column if exists extracted_text;

-- ------------------------------------------------------- job_post_brands --
-- Re-key from post_id-only to slide_id (keeping post_id denormalized).
alter table public.job_post_brands
  add column if not exists slide_id uuid references public.post_slides (id) on delete cascade;

-- Backfill: every existing job_post_brands row belongs to the post's single
-- backfilled slide (slide_index 0) -- unambiguous, since that 1:1 mapping is
-- exactly what the post_slides backfill above just created.
update public.job_post_brands b
set slide_id = ps.id
from public.post_slides ps
where ps.post_id = b.post_id
  and ps.slide_index = 0
  and b.slide_id is null;

alter table public.job_post_brands alter column slide_id set not null;

-- Swap the unique constraint from (post_id, brand) to (slide_id, brand) --
-- same dynamic-lookup-then-replace pattern as the status check above, since
-- the old constraint's autogenerated name can't be assumed.
do $$
declare
  existing_constraint_name text;
begin
  select con.conname into existing_constraint_name
  from pg_constraint con
  join pg_class rel      on rel.oid = con.conrelid
  join pg_namespace nsp  on nsp.oid = rel.relnamespace
  where con.contype = 'u'
    and nsp.nspname = 'public'
    and rel.relname = 'job_post_brands'
    and pg_get_constraintdef(con.oid) ilike '%post_id%'
    and pg_get_constraintdef(con.oid) ilike '%brand%'
  limit 1;

  if existing_constraint_name is not null then
    execute format('alter table public.job_post_brands drop constraint %I', existing_constraint_name);
  end if;
end $$;

alter table public.job_post_brands
  add constraint job_post_brands_slide_id_brand_key unique (slide_id, brand);

create index if not exists job_post_brands_slide_idx on public.job_post_brands (slide_id);
