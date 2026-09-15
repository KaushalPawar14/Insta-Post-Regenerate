-- ===========================================================================
--  Migration 005: lower the per-job post ceiling from 100 to 50
--
--  Run this ONCE in the Supabase dashboard: SQL Editor -> New query -> paste
--  -> Run. Idempotent -- safe to re-run.
--
--  supabase/schema.sql has also been updated to check (max_posts between 1
--  and 50), so a BRAND NEW project can just run schema.sql alone and skip
--  this file. This migration exists for the already-deployed database, where
--  schema.sql's `create table if not exists` is a no-op against existing
--  tables and CANNOT tighten an existing CHECK constraint.
--
--  MAX_POSTS_CEILING was already lowered to 50 in frontend/lib/types.ts and
--  backend/_lib/config.py -- this migration brings the database's own
--  ceiling (the third line of defence behind those two) in line with them.
-- ===========================================================================

-- Existing rows can only ever be <= 100 under the old constraint, and the
-- app itself never lets max_posts exceed the new ceiling going forward, so
-- there is nothing to backfill here -- only rows created between this
-- migration being written and being run could theoretically be 51-100, and
-- those are simply grandfathered (their existing job_posts rows are
-- untouched; only NEW jobs are capped at 50 requested posts).
--
-- The constraint's name is looked up dynamically rather than assumed (e.g.
-- as "jobs_max_posts_check", Postgres's usual auto-generated name for an
-- inline, unnamed column CHECK) -- guessing wrong here would either no-op
-- silently or leave two constraints in place. This finds whatever check
-- constraint is actually attached to jobs.max_posts and drops that one,
-- whatever it's called, then adds the new one under an explicit, stable name.
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
    and rel.relname = 'jobs'
    and pg_get_constraintdef(con.oid) ilike '%max_posts%'
  limit 1;

  if existing_constraint_name is not null then
    execute format('alter table public.jobs drop constraint %I', existing_constraint_name);
  end if;
end $$;

alter table public.jobs drop constraint if exists jobs_max_posts_check;
alter table public.jobs
  add constraint jobs_max_posts_check
  check (max_posts between 1 and 50);
