# Instagram AI Auto-Generator

A hosted, multi-tenant web app that ports an existing three-agent LangGraph
pipeline to Next.js on Vercel + Supabase + Upstash QStash.

Give it an Instagram profile (or a single post), and it scrapes the top posts,
has a vision model re-describe each image and rewrite its caption, then — **only
for posts you explicitly confirm** — generates a fresh branded image from that
description.

**Setup instructions: [SETUP.md](SETUP.md).**

---

## The protected prompts

Two prompts are the core IP of this project and live in
[`backend/_lib/prompts.py`](backend/_lib/prompts.py):

- `VISION_PROMPT` — the analyzer's system prompt
- `GENERATOR_PROMPT` — the `images.edit` compositing prompt

They were **extracted programmatically** from the original pipeline by
[`scripts/extract_prompts.py`](scripts/extract_prompts.py) rather than retyped,
so fidelity is guaranteed rather than assumed.

`VISION_PROMPT` is **byte-for-byte identical** to the original.

`GENERATOR_PROMPT` differs from the original by **exactly two lines** — the one
authorised edit, appended to the *Image-to-Text Transition* section:

```
+ * The black gradient overlay must begin exactly at the vertical midpoint of the
    "INSTAGRAM | FACTS4GENIUS" brand text line, so that the upper half of that text
    sits above the gradient start and the lower half sits within it.
+ * Do not begin the gradient any higher or lower than this point.
```

Nothing else changed: not the border rules, not the branding text, not the
layout instructions, not the wording of any other sentence.

The module **self-verifies against embedded SHA-256 checksums at import time**
and refuses to load if either prompt is edited. To re-check against the original
source at any time:

```bash
npm run verify:prompts
```

`data_vault/reference_format.png` was copied byte-for-byte (SHA-256 verified) to
`backend/_lib/assets/reference_format.png`.

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
  ├─ user presses Confirm ─────► POST /api/posts/[id]/confirm ──► QStash ──► /api/generate (backend service)
  │                                                     images.edit → Supabase Storage
  │
  └─◄── Supabase Realtime (+ fallback poll) pushes every status change back to the UI
```

### Why the Analyzer never triggers the Generator

The Scraper→Analyzer edge exists as a QStash message. The Analyzer→Generator
edge **does not exist at all**. `backend/analyze.py` finishes by writing
`awaiting_confirmation` and publishing nothing.

The only code path that can start a paid image generation is
`app/api/posts/[id]/confirm/route.ts`, reached by a human clicking Confirm. A
post nobody confirms simply waits forever — no timeout, no auto-generation.

`confirm-all` is a convenience wrapper that claims each awaiting post
individually and atomically; it doesn't bypass the rule.

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

## What changed from the original pipeline

| Original | Now | Why |
|---|---|---|
| `LangGraph StateGraph` edges | QStash messages | Each stage is a separate stateless invocation |
| `data_vault/1_scraped_json/` | `jobs` + `job_posts` rows | No filesystem on serverless |
| `data_vault/2_original_images/` | Storage, transient | Deleted once the generated image exists |
| `data_vault/3_extracted_prompts/` | `job_posts` columns | Queryable, and drives the UI |
| `data_vault/4_final_generated_posts/` | Storage, private bucket | Per-user signed URLs |
| `data_vault/temp_reference.png` | in-memory `BytesIO` | No disk |
| `.call()` blocking on Apify | `.start()` + polling | A 100-post scrape can outlast 300s |
| `images.edit(...)` | `+ quality=` | Hobby's hard 300s ceiling |
| — | `input_type: "post"` | New single-post-URL mode |
| — | `awaiting_confirmation` | New per-post confirmation gate |

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
| QStash free tier | 1,000 msgs/day, 10 parallel | ~5 maxed-out 100-post jobs/day |
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
