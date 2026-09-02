-- ===========================================================================
--  Migration 002: cost tracking + "removed" post status
--
--  Run this ONCE in the Supabase dashboard: SQL Editor -> New query -> paste
--  -> Run. Idempotent -- safe to re-run.
--
--  supabase/schema.sql has also been updated to include these changes, so a
--  BRAND NEW project can just run schema.sql alone and skip this file. This
--  migration exists for the already-deployed database, where schema.sql's
--  `create table if not exists` is a no-op against existing tables.
-- ===========================================================================

-- --------------------------------------------------------------- jobs -----
alter table public.jobs
  add column if not exists apify_total_cost_usd   numeric not null default 0,
  add column if not exists apify_cost_is_estimate boolean not null default true;

-- ----------------------------------------------------------- job_posts ----
alter table public.job_posts
  add column if not exists vision_cost_usd numeric not null default 0,
  add column if not exists image_cost_usd  numeric not null default 0,
  add column if not exists apify_cost_usd  numeric not null default 0;

-- Add 'removed' to the allowed status values. Postgres has no
-- "ADD VALUE IF NOT EXISTS" for a plain CHECK constraint, so this drops and
-- recreates it -- safe because dropping a CHECK constraint doesn't touch any
-- data, and the recreated constraint is a strict superset of the old one
-- (every value the old constraint allowed is still allowed here).
--
-- The constraint's name is looked up dynamically rather than assumed (e.g.
-- as "job_posts_status_check", Postgres's usual auto-generated name for an
-- inline, unnamed column CHECK) -- guessing wrong here would either no-op
-- silently or leave two constraints in place, with the old, stricter one
-- still rejecting 'removed'. This finds whatever check constraint is
-- actually attached to job_posts.status and drops that one, whatever it's
-- called, then adds the new one under an explicit, stable name.
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
  limit 1;

  if existing_constraint_name is not null then
    execute format('alter table public.job_posts drop constraint %I', existing_constraint_name);
  end if;
end $$;

alter table public.job_posts drop constraint if exists job_posts_status_check;
alter table public.job_posts
  add constraint job_posts_status_check
  check (status in ('pending','analyzing','awaiting_confirmation',
                    'queued_for_generation','generating','completed',
                    'failed_analysis','failed_generation','removed'));
