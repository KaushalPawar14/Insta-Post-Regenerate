-- ===========================================================================
--  Migration 007: slide-selection UI rework -- default unchecked
--
--  Run this ONCE in the Supabase dashboard: SQL Editor -> New query -> paste
--  -> Run. Idempotent -- safe to re-run.
--
--  supabase/schema.sql has also been updated to
--  `include_in_analysis boolean not null default false`, so a BRAND NEW
--  project can just run schema.sql alone and skip this file. This migration
--  exists for the already-deployed database, where schema.sql's
--  `create table if not exists` is a no-op against an existing table and
--  cannot change a column's default.
--
--  Context: the old per-slide checkbox + arrow-navigation review UI
--  defaulted every slide CHECKED. It's replaced by a "Select slides to
--  generate" dialog (plain numbered checkboxes, no images, submitted as one
--  batch) that defaults every slide UNCHECKED instead, matching this app's
--  established "no accidental spend" convention (the brand checkboxes on
--  Confirm & Generate already default unchecked). See
--  PATCH /api/posts/[id]/slide-selection.
-- ===========================================================================

alter table public.post_slides
  alter column include_in_analysis set default false;

-- Any post still sitting in awaiting_slide_selection hasn't been reviewed
-- under the NEW dialog's contract yet (it may have been scraped under the
-- old default-CHECKED UI, or never reviewed at all) -- reset its slides to
-- the new default so opening the dialog for it starts genuinely unchecked,
-- exactly as if it had just been scraped under the new rules. Posts that
-- have already left this status (analyzing or further) are untouched --
-- their selection was already locked in and acted on.
update public.post_slides ps
set include_in_analysis = false
from public.job_posts jp
where jp.id = ps.post_id
  and jp.status = 'awaiting_slide_selection';
