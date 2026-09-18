# Instagram Post Generator

A hosted, multi-tenant web app that ports an existing three-agent LangGraph
pipeline to Next.js on Vercel + Supabase + Upstash QStash.

Give it an Instagram profile (or a single post), and it scrapes the top posts,
has a vision model re-describe each image and rewrite its caption, then — **only
for posts you explicitly confirm** — generates a fresh branded image from that
description.

**Setup instructions: [SETUP.md](SETUP.md).**

---

## The protected prompts

Three prompts are the core IP of this project and live in
[`backend/_lib/prompts.py`](backend/_lib/prompts.py):

- `VISION_PROMPT` — the analyzer's system prompt
- `GENERATOR_PROMPT` — the Facts4Genius `images.edit` compositing prompt
- `FACTSBYTES_GENERATOR_PROMPT` — the Facts Bytes `images.edit` compositing prompt

The first two were **extracted programmatically** from the original pipeline by
[`scripts/extract_prompts.py`](scripts/extract_prompts.py) rather than retyped,
so fidelity is guaranteed rather than assumed.

`VISION_PROMPT` runs once per CHECKED slide instead of once per post (see
"Carousel / multi-slide posts") — a plain image post still runs it exactly
once, same as always. It differs from the original by **exactly two
appended lines**, added after its last existing bullet — nothing earlier in
the prompt moved or reworded:

```
+ * Always describe any diagram, hologram, X-ray, or anatomical/mechanical overlay
    in full detail as an essential part of the scene -- never omit or shorten it.
+ * Exclude ALL visible text in the scene, not just logos/captions -- book titles,
    signs, labels, screens, clothing text included. Describe such objects by
    appearance only, never mentioning any words on them.
```

The first addition exists because holographic/diagrammatic elements in
source images were sometimes dropped from the generated image entirely, since
the description didn't treat them as essential. The second exists because
incidental text on objects in the scene (a book's title, a sign) was leaking
into the visual description, causing the image model to render that text —
which then visually collides with the separately-applied caption text in the
final generated image.

`GENERATOR_PROMPT` differs from the original by **exactly two authorised
edits**: the gradient-position lines appended to the *Image-to-Text
Transition* section, and the text-color instruction's "yellow" changed to
the exact hex `#f6ff02`:

```
+ * The black gradient overlay must begin exactly at the vertical midpoint of the
    "INSTAGRAM | FACTS4GENIUS" brand text line, so that the upper half of that text
    sits above the gradient start and the lower half sits within it.
+ * Do not begin the gradient any higher or lower than this point.

- * Use only white and yellow text.
+ * Use only white and #f6ff02 text.
```

The thin **border** ("Preserve the thin yellow border.") is still described
as plain "yellow" — only the text-color instruction was approved for the hex
swap. Nothing else changed: not the border rules, not the branding text, not
the layout instructions, not the wording of any other sentence.

`FACTSBYTES_GENERATOR_PROMPT` has no original pipeline file to extract from —
it was provided directly and copied verbatim into
[`scripts/factsbytes_prompt_source.txt`](scripts/factsbytes_prompt_source.txt),
its own canonical source-of-truth artifact. It now has its **first-ever
approved edit**, applied the same programmatic way as `GENERATOR_PROMPT`'s:
the text-color bullet's two "yellow" mentions changed to `#f6ff02`:

```
- • Use ONLY bright yellow and white text. Highlight important portions in yellow
    and keep remaining portions white.
+ • Use ONLY bright #f6ff02 and white text. Highlight important portions in
    #f6ff02 and keep remaining portions white.
```

The divider **lines** ("Place two thin horizontal yellow lines...") are still
described as plain "yellow" — only the text-color bullet was approved for the
hex swap.

The module **self-verifies against embedded SHA-256 checksums at import time**
for all three prompts, and refuses to load if any is edited. To re-check
against the original sources at any time:

```bash
npm run verify:prompts
```

`data_vault/reference_format.png` was copied byte-for-byte (SHA-256 verified) to
`backend/_lib/assets/reference_format.png` (Facts4Genius). The Facts Bytes
template lives alongside it at
`backend/_lib/assets/reference_format_factsbytes.png`.

---

## Architecture

One Vercel project, deployed as two [Vercel Services](https://vercel.com/docs/services)
behind a single domain, split by top-level rewrites in [`vercel.json`](vercel.json):

- **`frontend`** (root `frontend/`) — the Next.js app: UI + Node API routes
- **`backend`** (root `backend/`) — the Python pipeline functions

Vercel routes `/api/scrape`, `/api/scrape_poll`, `/api/analyze`, and
`/api/generate` straight to `backend`; every other path goes to `frontend`.
Each service receives the request's full original path, so the Python code
and every QStash-published URL are unaffected by the split.

```
Browser (frontend service, Next.js on Vercel)
  │  anonymous Supabase session; every row + image scoped to it by RLS
  │
  ├─ POST /api/jobs ──────────► creates job row ──► QStash ──► /api/scrape (backend service)
  │                                                                 │
  │                                          starts Apify run, schedules poll
  │                                                                 ▼
  │                                             QStash ──► /api/scrape_poll (backend service)
  │                                                     sorts by likes, inserts rows,
  │                                                     fans out one message per post
  │                                                                 ▼
  │                                                QStash ──► /api/analyze (backend service)
  │                                                     vision LLM → awaiting_confirmation
  │                                                                 │
  │                                          ═══ HARD STOP. NO MESSAGE PUBLISHED. ═══
  │                                                                 │
  ├─ user picks format(s), presses Confirm & Generate ──► POST /api/jobs/[id]/confirm-all
  │                                                     one job_post_brands row per selected
  │                                                     format per post ──► QStash (one message
  │                                                     per row) ──► /api/generate (backend service)
  │                                                     images.edit → Supabase Storage
  │
  └─◄── Supabase Realtime (+ fallback poll) pushes every status change back to the UI
```

### Why the Analyzer never triggers the Generator

The Scraper→Analyzer edge exists as a QStash message. The Analyzer→Generator
edge **does not exist at all**. `backend/analyze.py` finishes by writing
`awaiting_confirmation` and publishing nothing.

The only code path that can start a paid image generation is
`app/api/jobs/[id]/confirm-all/route.ts`, reached by a human picking at least
one format and clicking **Confirm & Generate** — the sole confirmation action
in the UI (see [Remove + Confirm All](#remove--confirm-all) and
[Multi-brand generation](#multi-brand-generation)). A post nobody confirms
simply waits forever — no timeout, no auto-generation. Its per-row claim is
atomic (`UPDATE ... WHERE status = 'awaiting_confirmation'`), so a rapid
double-click can never publish two generate messages for the same post+format.

### Why no worker service is needed

QStash's free tier allows a destination endpoint **up to 15 minutes** to respond.
Vercel's Hobby ceiling is **300 seconds**. Since 300s < 15min, QStash simply
holds the request open until the Python function returns.

Each invocation handles exactly one stage for one post, so nothing ever needs to
outlive a single function call. **No Railway, Render, or Fly.io required** — the
`backend` service is itself just Vercel Functions, not an always-on process.

The one thing that would force a worker is a single generation exceeding 300s —
which is exactly what `IMAGE_QUALITY=medium` is there to prevent.

---

## The 300-second ceiling

**Verified against current docs:** Vercel Hobby is **300s default *and* maximum**
with Fluid compute. Unlike Pro (800s), there is no way to raise it.

gpt-image-2 at `1024x1536` with `quality` unset (`auto`) has been benchmarked at
roughly **195s median, 280s worst case** at high quality. That's inside 300s, but
with almost no margin.

So `backend/generate.py` passes `quality` explicitly, defaulting to `medium`. This is
the **only** change to the `images.edit` call — the prompt is untouched. Override
with the `IMAGE_QUALITY` env var (`low` | `medium` | `high` | `auto`); only raise
it above `medium` if you move to a Vercel plan with a higher ceiling.

### Not double-spending on retries

Generation is the only step that costs real money, so it's defended twice:

1. **`retries: 0`** on the generate message. A retry after a 300s timeout cannot
   know whether OpenAI already produced and billed for an image, so it fails
   visibly instead of retrying blind.
2. **Atomic claim.** `UPDATE ... WHERE id = ? AND status = 'queued_for_generation'`
   is atomic in Postgres, so a duplicate delivery matches zero rows and returns
   immediately.

A generation abandoned at the ceiling is detected client-side (`generate_started_at`
older than 330s) and surfaced as **Retry generation** — a deliberate, user-driven
re-spend.

Analysis is cheap, so it *does* auto-retry and recovers abandoned work.

---

## Multi-tenant isolation

Every visitor gets a silent **Supabase anonymous session** on first load. That
identity is the isolation boundary:

- RLS on `jobs` and `job_posts` — `user_id = auth.uid()` for select/insert/update/delete
- Storage objects live at `<user_id>/<job_id>/<file>`, and the Storage policies
  match the first path segment against `auth.uid()`
- The bucket is **private**; the browser reads images through short-lived signed URLs
- API routes verify the caller's bearer token with Supabase, then scope every
  query by `user_id` — the service-role key bypasses RLS, so this is not optional

Because there's no login, clearing browser storage loses access to previous jobs.
That's the trade-off of a no-signup app.

### Sharing a job

The **Share** button (job page and history list) is the one deliberate hole
in this isolation, and it's scoped as narrowly as possible on purpose:

- `POST /api/jobs/[id]/share` mints (or returns the existing)
  `jobs.share_token` — a fresh `crypto.randomUUID()`, never the job's own
  database id — and is itself owner-scoped (`user_id = caller`) like every
  other write in this app.
- `GET /api/share/[token]` is the only public, unauthenticated read in the
  entire app. It looks up the job by `share_token` alone, using the
  service-role key, and returns only `{id, post_id, caption, image_url}` for
  posts with `status = 'completed'` — no other status, no cost figures, no
  other job's data, ever.
- **No RLS policy grants the `anon` role any access** to `jobs`, `job_posts`,
  or `storage.objects` — every policy in `schema.sql` is `to authenticated`
  only. A naive "allow anon to select jobs where share_token is not null"
  policy would let any visitor enumerate *every* shared job across *every*
  user, since RLS can't verify "the caller knows this specific token" — it
  can only gate which rows a role sees once granted access to the table at
  all. Enforcing the token match entirely in `/api/share/[token]/route.ts`,
  with zero anon grants on the underlying tables, avoids that enumeration
  risk structurally rather than relying on a policy staying correctly
  written forever.
- The public `/share/<token>` page skips the anonymous-session bootstrap
  (`components/SessionBoot.tsx`) entirely — it never queries Supabase
  directly, only this one route, so it has no need for a session and no
  anonymous Supabase user gets created for a random link click.
- It's read-only: the route file exports a `GET` handler and nothing else.
  There is no way to confirm, remove, retry, or delete anything through it.

---

## Data lifecycle

**Nothing is deleted automatically.** No TTL, no cron, no expiry.

- **History** — `/history` lists every job, including posts still awaiting confirmation
- **Delete** — always available, on both the job page and in history; removes the
  rows and every Storage object
- **Gentle nudge** — once *every* post in a job is completed **and** downloaded
  (tracked by the `downloaded` boolean), the job page suggests deleting it. It is
  only ever a suggestion.

Original scraped images are **transient**: the thumbnail exists only so you can
see what you're confirming, and is deleted from Storage the moment the generated
image lands. Completed posts show the generated image only.

> **Storage is your real constraint.** Supabase's free tier gives 1 GB, and a
> 1080×1350 PNG is ~1.5–3 MB — so roughly **350–600 generated images**. The
> delete nudge is doing real work here, not just tidiness.

---

## Access control

Fully open by default: no login, no rate limiting, no per-hour cap. This is a
deliberate choice.

The per-post confirmation step is the real spend control — image generation, the
expensive part, only ever runs on posts a human actively approved.

An optional password gate ships **off**. Set `SITE_PASSWORD` and redeploy to turn
it on; unset it to turn it off.

---

## Cost tracking

Every job shows a running total, and every completed post shows its own cost,
in **₹ INR**, labeled **"Estimated cost"** throughout.

**OpenAI only — vision (gpt-5) and image generation (gpt-image-2), computed
from real, API-reported token usage, not a flat guess:**

- `backend/analyze.py` calls `llm.with_structured_output(schema, include_raw=True)`
  so the raw `AIMessage.usage_metadata` (a dict at runtime — confirmed against
  the installed `langchain-core`, not assumed) is available, and multiplies
  its real `input_tokens` / `output_tokens` by gpt-5's per-token price.
- `backend/generate.py` reads `ImagesResponse.usage` from the real
  `images.edit()` response (confirmed against the installed `openai` SDK's
  response model) — which splits input into text tokens and image tokens
  (a reference image, like ours, bills at the image-token rate) — and
  multiplies each by gpt-image-2's per-token price. This runs once per
  confirmed **format**, so a post generated in both Facts4Genius and Facts
  Bytes carries two independent image-generation costs (see
  [Multi-brand generation](#multi-brand-generation)).

Both price tables live in [`backend/_lib/pricing.py`](backend/_lib/pricing.py),
verified against OpenAI's official pricing page on 2026-09-02:

| | Input | Output |
|---|---|---|
| gpt-5 | $1.25 / 1M tokens | $10.00 / 1M tokens |
| gpt-image-2 | $5.00 / 1M text tokens, $8.00 / 1M image tokens | $30.00 / 1M tokens |

**Apify cost is deliberately excluded — not shown, not even as ₹0.** The user
runs Apify on free credits and wants it treated as $0 / not applicable, not
displayed as a labeled estimate. `scrape_poll.py` never computes or writes
`job_posts.apify_cost_usd` / `jobs.apify_total_cost_usd` / `apify_cost_is_estimate`
(they stay at their schema default), and the frontend's cost total
(`postCostUsd()` in `lib/types.ts`) only ever sums `vision_cost_usd +
image_cost_usd`. The real-usage-based calculation this replaced
(`pricing.apify_cost_usd()`, `Run.usage_total_usd` when available, falling
back to `APIFY_ESTIMATED_COST_PER_POST_USD`) is left in the code, unused,
rather than deleted — the lowest-risk way to bring it back if the
free-credits situation changes; see `_lib/pricing.py` and `.env.example`.

Every remaining cost is stored in **USD** — `vision_cost_usd` on `job_posts`
(one vision call per post, shared across formats), and `image_cost_usd` on
each `job_post_brands` row (one per confirmed format) — and summed per post
by `postCostUsd()` in `lib/types.ts`. INR is a **display-only** conversion the
frontend performs with a **fixed** rate from `NEXT_PUBLIC_USD_TO_INR_RATE`
(default `94.85`, the midpoint of two sources for the 2026-09-01 USD/INR spot
rate). No live currency API is ever called — one less dependency, and a job's
displayed cost can't shift mid-run because the rate moved.

## Remove + Confirm All

There is no per-post Confirm button. Each `awaiting_confirmation` post has a
**Remove** button instead — it atomically claims that one row
(`awaiting_confirmation → removed`, the same conditional-`UPDATE` pattern used
everywhere else in this codebase for idempotency) and the post is excluded
from generation **permanently**: `removed` is a terminal status, shown
distinctly in the UI (a dimmed card, a dashed "excluded" strip, a `–` marker
on its progress stepper), never silently hidden.

**Confirm & Generate** (`app/api/jobs/[id]/confirm-all/route.ts`) is the only
way to trigger generation. The user first picks one or both output formats via
checkboxes — both start **unselected**; clicking the button with none checked
submits nothing and shows an inline "Select at least one format" message
instead. Once at least one format is picked, the route queries every post
still in `awaiting_confirmation` for the job and atomically claims each one
individually, then inserts one `job_post_brands` row and publishes one
generate message **per selected format**, per post — removed posts are
excluded **by construction** (the query itself only ever matches
`awaiting_confirmation` rows), not by any extra filtering logic that could be
forgotten. A rapid double-click races two calls against the same rows; the
second call's conditional `UPDATE` matches zero rows for anything the first
call already claimed, so it publishes nothing for those — verified with a
dry-run simulation of concurrent calls before this shipped (see the PR/commit
message for the specific cases it checks).

`refresh_job_status` (`backend/_lib/pipeline.py`) treats `removed` the same as
`completed`/`failed_*` when deciding whether a job still has pipeline work
left — otherwise a job where every remaining post gets removed (none
confirmed) would never resolve out of a generic "still working" status.

---

## Multi-brand generation

A post can now be generated in **either or both** output formats —
Facts4Genius (the original branded template) and Facts Bytes (a second,
independently-branded template using its own protected prompt and its own
reference image, `backend/_lib/assets/reference_format_factsbytes.png`) —
without duplicating any of the scrape/vision work.

**Schema: a separate `job_post_brands` table, not parallel columns.** A naive
approach would add `facts4genius_status`, `facts4genius_image_path`,
`factsbytes_status`, `factsbytes_image_path`, ... directly onto `job_posts`.
That was rejected because a post's number of generations is no longer fixed
at one — it's 0, 1, or 2 per slide, and the moment a third format ever
exists, every column doubles again. `job_post_brands`
(`supabase/migration_004_multi_brand.sql`) instead has one row per
`(slide_id, brand)` — each with its own `status`, `final_image_path`,
`image_cost_usd`, and error fields. This is the same "outgrows fixed
columns" reasoning already applied once in this codebase when per-post
generation state was pulled out of a single wide row, kept consistent
rather than special-cased for brands — and, later, re-keyed from `post_id`
to `slide_id` for the same reason again when carousels arrived (see
"Carousel / multi-slide posts" below); `post_id` stays denormalized
alongside `slide_id` on every row specifically so this section's own rollup
design keeps working unmodified either way.

**`job_posts.status` becomes a rollup, not a directly-written value.** The
Generator used to `UPDATE job_posts SET status = ...` on its own row.  Now it
writes to that post's `job_post_brands` row instead, and
`refresh_post_status()` (`backend/_lib/pipeline.py`) recomputes
`job_posts.status` from all of that post's brand rows via
`compute_post_rollup_status()`:

| Brand rows | Post status |
|---|---|
| Any row `generating` | `generating` |
| Some (not all) rows still `queued_for_generation` | `generating` |
| All rows `queued_for_generation` | `queued_for_generation` |
| All rows terminal, ≥1 `completed` | `completed` |
| All rows terminal, none `completed` | `failed_generation` |

The payoff of this rollup design: `job_posts.status` keeps its exact original
set of values and meaning, so `PostStepper`, the stage-breakdown UI, and the
ETA calculation in `lib/eta.ts` needed **no logic changes** — only `eta.ts`
and `JobProgress` had their signatures extended to also accept `brands[]`, for
cost summation. Every place in the app that already reasoned about post
status keeps working unmodified, whether a post ends up with one generation
or two.

**Display.** `components/BrandToggle.tsx` is a small tab control shown only
when a post has more than one `job_post_brands` row; it's shared verbatim
between the results view (`PostCard.tsx`), the public share page
(`share/[token]/page.tsx`), and any post reached from History — one
component, not three reimplementations. Download always targets whichever
brand is currently selected in the toggle.

**Generating the missing format later.** A slide confirmed for only one
format shows an **"Also generate for Facts Bytes"** (or Facts4Genius) button,
backed by `POST /api/slides/[id]/generate-brand`. It reuses that slide's
existing Analyzer output — it never re-scrapes or re-runs the vision call —
so the only new cost is that one additional `images.edit` call, and it
never touches any other slide of the same post. The same route powers
**Retry** for a single failed format, atomically claiming
`failed_generation → queued_for_generation` on that one `job_post_brands` row
so a retry can never duplicate a completed sibling format.

**History list.** Each job row shows a small tag for every format with at
least one `completed` `job_post_brands` row anywhere in that job (across all
its posts) — a job-level summary only. Which specific post has which format
is still shown on that post's own card once the job is opened, not on the
History row.

---

## Carousel / multi-slide posts

Instagram carousels ("Sidecar" posts, `type: "Sidecar"` in the Apify item)
have several images, not one. Every post — a plain image or a carousel —
now has one or more **slides**, and each slide gets its own Analyzer output,
its own per-brand generations, and its own display.

**Detecting a carousel.** Confirmed against two real sample Apify actor
outputs before writing any code: a plain image post has `type: "Image"` and
an empty `images` array (its one image lives in `displayUrl`); a Sidecar has
`type: "Sidecar"` and its `images` array holds the ordered slide URLs,
matching `childPosts[i].displayUrl` 1:1 by index. This holds for both
profile-scraped and single-post-URL jobs, because `scrape.py` already sends
the SAME actor the SAME `directUrls` + `resultsType: "posts"` shape for
both modes (differing only in `resultsLimit`) — a post's own item shape is a
function of the post's type, not of which mode fetched it.

**Schema: a new `post_slides` table, and `job_post_brands` re-keyed to it.**
`post_slides` has one row per slide (`unique(post_id, slide_index)`),
carrying everything that used to live directly on `job_posts` but can no
longer be a single value once a post can have several images: the durable
thumbnail, the stage-2 selection flag (`include_in_analysis`, defaulting
**unchecked** — see "Selecting slides" below), and each slide's own Agent 2
output (`image_generation_prompt`, `extracted_text`, a `refined_caption`
candidate, `vision_cost_usd`). `job_post_brands` gains
`slide_id` (`unique(slide_id, brand)` replaces `unique(post_id, brand)`) but
**keeps `post_id` denormalized** — every existing post-level rollup query
(`refresh_post_status`, the History brand-tag aggregation) still just asks
"give me all this post's brand rows" and gets the right answer whether the
post has one slide or several, with zero changes to that code.
`job_posts.image_generation_prompt`/`extracted_text` are **dropped**, not
just deprecated, once backfilled onto each post's one slide — a single-value
column literally cannot represent a multi-slide post, so leaving it around
would be actively misleading rather than a safety net. See
`supabase/migration_006_carousel_slides.sql` for the exact backfill.

**Durable slide images, downloaded before the user ever sees them.** Stage 2
(below) introduces a genuinely new, human-gated pause between scraping and
analysis. Instagram's CDN URLs already "expire quickly" (see Known
constraints) — tolerable today because analysis fires immediately after
scraping with no wait, but a carousel can now sit waiting on a reviewer
indefinitely. Two things need a copy that outlives that wait: stage 2's own
preview, and any slide the reviewer leaves unchecked, whose "final"
appearance in the result is its *original* image, shown for as long as the
job exists (this app has no TTL at all). So a new stage, **`prepare_slides.py`**,
runs once per carousel right after scraping and downloads every slide into
our own Storage bucket before stage 2 is ever shown — reusing the exact
download → RGB → resize → upload logic `analyze.py` already had for the
single-image thumbnail, just moved one stage earlier and run per slide. A
plain image post skips this entirely; its one slide is still fetched live at
analysis time, exactly as before this feature existed.

**Pipeline, in order:**

```
scrape_poll.py
  │
  ├─ plain image post ──────────────────► analyze (unchanged, immediate)
  │
  └─ carousel ──► prepare_slides (download every slide into Storage)
                          │
                          ▼
             job_posts.status = awaiting_slide_selection
                          │
        "Select slides to generate" dialog -- plain numbered checkboxes
        (1..N), no images, no per-slide fetch, ALL UNCHECKED by default;
        the whole selection is submitted in one batch on Confirm
        (PATCH /api/posts/[id]/slide-selection) -- no cost incurred yet
        beyond the already-sunk scrape
                          │
        "Continue to analysis" -- ONE batch action across every carousel
        in the job (mirrors Confirm & Generate's own job-level batching,
        not a per-post button), per-post selection already done above
                          │
                          ▼
     fan out `analyze` ONLY for checked slides; unchecked slides are
     stamped `skipped` and never touched again
                          │
                          ▼
        awaiting_confirmation (existing stage, unchanged: caption +
        brand checkboxes + Confirm & Generate)
                          │
        Confirm & Generate: one job_post_brands row per
        (checked slide × selected brand)
```

**Two rollups instead of one.** `job_posts.status` was already a rollup over
`job_post_brands` for the generation phase (see Multi-brand generation
above); it's now ALSO a rollup over `post_slides` for the analysis phase,
via a second pure function, `compute_post_analysis_rollup_status()`, with
the identical shape (any relevant slide analyzing/pending → `analyzing`; all
terminal with ≥1 analyzed → `awaiting_confirmation`; all terminal with none
analyzed → `failed_analysis`) just at a different stage. A plain image
post's one slide collapses this rollup to exactly its pre-carousel
transitions, so nothing about its behavior changes. `jobs.status` itself
gained **no new value** — a carousel's `awaiting_slide_selection` folds into
the same job-level "needs your attention" bucket `awaiting_confirmation`
already meant, the same way that bucket already covers many different
per-post states without the job-level enum needing to enumerate all of them.

**Caption: promoted from the first checked slide, once, race-safely.** Every
checked slide's own vision call independently produces a candidate
`refined_caption` (stored on its own slide row), but the post still shows
exactly one, editable caption, as before. It's promoted from whichever
checked slide has the lowest `slide_index`, computed **only** inside the
same rollup call that finds every checked slide already terminal — there is
exactly one such call (made by whichever slide happens to finish last), and
by then `include_in_analysis` is immutable (enforced in
`PATCH /api/posts/[id]/slide-selection`, rejected once the post leaves
`awaiting_slide_selection`), so the "first checked slide" the promotion
computes can never change out from under it.

**Selecting slides.** `components/SlideSelectDialog.tsx` renders purely from
slide data already loaded into the job page's state (via `useJob`) — no
image loading, no navigation, no per-slide fetch, so it opens instantly even
for a post with many slides. It's a local draft: checking boxes does nothing
server-side until Confirm, which sends the WHOLE selection in one batch call
rather than one request per toggle. Every slide starts **unchecked**,
matching this app's established "no accidental spend" convention (the brand
checkboxes on Confirm & Generate default unchecked too) — the user opts
individual slides IN, rather than opting unwanted ones out.

**Display.** `components/SlideNav.tsx` is the arrow-navigation counterpart to
`BrandToggle` — same self-hides-below-2 convention, same
results-page/History/share-page reuse. A slide shows its generated image (with
`BrandToggle` nested inside, if more than one format was generated for it),
or — for a slide skipped in stage 2, or whose own analysis failed — its
durable original, permanently. Download targets whichever slide+brand is
currently in view. Cost tracking sums real per-slide vision cost (checked
slides only) and real per-slide-per-brand generation cost, the same
summation pattern `job_post_brands` already established, one level deeper.

---

## What changed from the original pipeline

| Original | Now | Why |
|---|---|---|
| `LangGraph StateGraph` edges | QStash messages | Each stage is a separate stateless invocation |
| `data_vault/1_scraped_json/` | `jobs` + `job_posts` rows | No filesystem on serverless |
| `data_vault/2_original_images/` | Storage, transient | Deleted once the generated image exists |
| `data_vault/3_extracted_prompts/` | `job_posts` columns | Queryable, and drives the UI |
| `data_vault/4_final_generated_posts/` | Storage, private bucket | Per-user signed URLs |
| `data_vault/temp_reference.png` | in-memory `BytesIO` | No disk |
| `.call()` blocking on Apify | `.start()` + polling | A 50-post scrape can outlast 300s |
| `images.edit(...)` | `+ quality=` | Hobby's hard 300s ceiling |
| — | `input_type: "post"` | New single-post-URL mode |
| — | `awaiting_confirmation` | New per-post confirmation gate |
| per-post Confirm button | Remove + Confirm & Generate | One atomic, job-level confirmation action; removed posts excluded by construction |
| — | `removed` status | Terminal off-ramp for a post excluded before confirmation |
| — | real per-post/per-job cost in ₹ INR (OpenAI only) | `with_structured_output(..., include_raw=True)` and `ImagesResponse.usage` expose real token counts; Apify excluded entirely (free credits) |
| single branded template | `job_post_brands` table, Facts4Genius + Facts Bytes | A post can be generated in either or both formats, reusing one Analyzer output |
| one image per post | `post_slides` table, carousel-aware | A post can have several slides, each independently reviewed, analyzed, and generated |
| — | `awaiting_slide_selection` status + stage-2 review | New pre-analysis gate for carousels only -- instant numbered-checkbox dialog, zero AI cost until Continue |

**Dependencies dropped:**

- **`langchain-groq` — verified genuinely unused.** No reference in
  `agent_1_scraper.py`, `agent_2_analyzer.py`, `agent_3_generator.py`, `main.py`,
  or `state.py`. It appeared only as an orphan `requirements.txt` line alongside
  an unused `GROQ_API_KEY`. Removed.
- **`langgraph`** — in-process orchestration is now QStash message chaining.
- **`python-dotenv`** — Vercel injects environment variables directly.

The agent logic itself, the Apify integration, the field extraction, the
sort-by-likes, the vision call, and the `images.edit` call are otherwise carried
over as-is.

---

## Known constraints

| Thing | Limit | Consequence |
|---|---|---|
| Vercel Hobby function duration | **300s hard** | Drives `IMAGE_QUALITY=medium` |
| Vercel Hobby licence | Non-commercial | Fine for a free tool; relevant if this monetises |
| QStash free tier | 1,000 msgs/day, 10 parallel | ~6 maxed-out 50-post jobs/day (both formats) |
| Supabase free storage | 1 GB | ~350–600 generated images |
| Supabase free DB / egress | 500 MB / 5 GB | Not a near-term concern |
| Supabase free projects | **Pause after 7 days idle** | An unused deployment goes cold |
| Apify `likesCount` | Returns `-1` when hidden | Normalised to `0` so the sort isn't corrupted |
| Instagram CDN URLs | Expire quickly | A long-delayed analyze can 404; surfaced as `failed_analysis` |

### Runtime coexistence

Next.js and Python don't share a build the way early community write-ups
describe (a raw root-level `/api/*.py` folder sitting next to a Next.js app in
one project). As of the current Vercel docs, that combination is not
auto-discovered — deploying it produces `The pattern "api/**/*.py" ... doesn't
match any Serverless Functions`. The supported mechanism for combining two
runtimes in one project is [**Services**](https://vercel.com/docs/services):
each runtime is declared as a named service with its own root directory, and
public routing between them is entirely owned by `vercel.json`'s top-level
`rewrites` — never by file placement. Hence the `frontend/` / `backend/`
sibling-directory split (see [Architecture](#architecture)), matching Vercel's
own worked example directory-for-directory.

Paths still don't overlap, now enforced by explicit rewrites instead of by
convention:

- `backend` service: `/api/scrape`, `/api/scrape_poll`, `/api/analyze`, `/api/generate`
- `frontend` service (Next.js's own `app/api/*`): `/api/jobs/*`, `/api/posts/*`, `/api/gate`

One further wrinkle: Services also requires a Python service to declare a
**single ASGI/WSGI `entrypoint`** — unlike the older non-Services mode, it does
not support multiple standalone files each becoming their own function. So
`backend/main.py` is one small FastAPI app (`entrypoint: "main:app"` in
`vercel.json`) that owns request/response plumbing only — signature
verification, body parsing, error-to-status-code mapping — and dispatches to
`scrape.py` / `scrape_poll.py` / `analyze.py` / `generate.py`'s plain
`run(payload) -> dict` functions, which hold 100% of the actual pipeline logic
and are otherwise untouched by this. FastAPI here is purely this service's
internal transport; because Services routes between `frontend` and `backend`
entirely at the platform level, it has no way to affect the Next.js service's
own routing, unlike the "framework preset hijacks everything" risk that
applied to the earlier, discarded non-Services approach.

Verify after your first deploy with the health checks in
[SETUP.md §3.4](SETUP.md#34-confirm-the-python-functions-are-live--do-this-first).

### Legal

Scraping Instagram via a third-party actor is against Instagram's Terms of
Service regardless of the tooling used, and running it as a publicly hosted
multi-tenant service is a materially different risk posture than a personal
script. Actor breakage and partial results under volume are an ongoing
maintenance cost, not a one-time build risk.

---

## Project layout

```
vercel.json                 ★ Services config: frontend + backend, top-level rewrites
supabase/schema.sql         tables + RLS + Storage + Realtime (run once)
scripts/                    prompt extraction + integrity guard

backend/                    Vercel Service "backend" (Python, FastAPI entrypoint)
  requirements.txt
  main.py                   ★ the ASGI entrypoint — request plumbing only
  scrape.py                 Agent 1a — start the Apify run
  scrape_poll.py            Agent 1b — poll, sort by likes, fan out
  analyze.py                Agent 2  — vision LLM → awaiting_confirmation
  generate.py               Agent 3  — images.edit → Storage
  _lib/
    prompts.py              ★ PROTECTED — the two prompts, checksum-guarded
    assets/reference_format.png   ★ byte-identical copy of the template
    schemas.py              ported Pydantic shapes
    config.py  db.py  queue.py  handler.py  pipeline.py

frontend/                   Vercel Service "frontend" (Next.js)
  package.json
  app/                      App Router pages + Node API routes
  components/               SessionBoot, JobProgress, PostCard
  lib/                      Supabase clients, QStash, ETA, URL parsing, types
  middleware.ts             optional SITE_PASSWORD gate
```

Each service builds independently from its own root (its own `requirements.txt`
or `package.json`), exactly as Vercel's Services model expects — see
[Runtime coexistence](#runtime-coexistence).
