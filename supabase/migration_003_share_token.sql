-- ===========================================================================
--  Migration 003: public "share this job" feature
--
--  Run this ONCE in the Supabase dashboard: SQL Editor -> New query -> paste
--  -> Run. Idempotent -- safe to re-run.
--
--  supabase/schema.sql has also been updated to include this change, so a
--  BRAND NEW project can just run schema.sql alone and skip this file.
--
--  IMPORTANT: this migration does NOT add any RLS policy granting the `anon`
--  role access to `jobs` or `job_posts`. The public /share/<token> page never
--  queries Supabase directly from the browser -- it only calls a dedicated
--  Next.js API route (app/api/share/[token]/route.ts) that uses the
--  service-role key server-side, looks up the job by share_token, and
--  returns only the minimal fields needed (completed posts' image + caption).
--  A naive "allow anon to select jobs where share_token is not null" RLS
--  policy would let any anonymous visitor list EVERY shared job across every
--  user (not just the one they have a link for), since RLS cannot verify
--  "the caller knows this specific token" as a credential -- it can only
--  filter which ROWS are visible once a role is granted access to a table at
--  all. Keeping `anon` with zero grants on these tables and enforcing the
--  token match in application code instead avoids that enumeration risk
--  entirely. See README "Sharing a job" for the full explanation.
-- ===========================================================================

alter table public.jobs
  add column if not exists share_token text unique;
