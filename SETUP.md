# Setup — step by step

You need three accounts. All three are free; the only things you pay for are
OpenAI and Apify usage.

| Service | Cost | What it does here |
|---|---|---|
| **Vercel** | Free (Hobby) | Hosts the Next.js UI *and* the Python pipeline functions |
| **Supabase** | Free | Postgres (job state), Storage (images), Realtime (progress), anonymous auth |
| **Upstash QStash** | Free | Queues each pipeline stage |

You do **not** need Railway, Render, Fly.io, or any always-on server. See
[README.md](README.md#why-no-worker-service-is-needed) for why.

---

## 1. Supabase (~10 minutes)

### 1.1 Create the project

1. Go to <https://supabase.com> → **Start your project** → sign in with GitHub.
2. **New project**.
   - **Name:** `instagram-ai-generator`
   - **Database Password:** click Generate, then **save it in your password
     manager**. You won't need it for this app (we use API keys), but it can't
     be recovered later.
   - **Region:** pick the one closest to you.
   - **Plan:** Free.
3. Click **Create new project** and wait ~2 minutes for provisioning.

### 1.2 Create the tables, RLS policies, and Storage bucket

1. In the left sidebar: **SQL Editor** → **New query**.
2. Open `supabase/schema.sql` from this repo, copy the **entire** contents, and
   paste it in.
3. Click **Run**. You should see `Success. No rows returned`.

This one script creates the `jobs` and `job_posts` tables, every Row Level
Security policy, the Realtime publication, and the private `generated` Storage
bucket. It's safe to re-run.

### 1.3 Enable anonymous sign-ins  ← easy to miss, nothing works without it

1. Sidebar: **Authentication** → **Sign In / Providers**.
2. Find **Anonymous sign-ins** and toggle it **on**. Save if prompted.

Takes effect immediately, no redeploy.

### 1.4 Verify Realtime is on

1. Sidebar: **Database** → **Publications** → `supabase_realtime`.
2. Confirm `jobs` and `job_posts` are both listed. (The SQL script adds them; this
   is just a sanity check.)

### 1.5 Copy your keys

1. Sidebar: **Project Settings** → **Data API**. Copy:
   - **Project URL** → `NEXT_PUBLIC_SUPABASE_URL`
2. **Project Settings** → **API Keys**. Copy:
   - **anon / public** → `NEXT_PUBLIC_SUPABASE_ANON_KEY`
   - **service_role** (click to reveal) → `SUPABASE_SERVICE_ROLE_KEY`

> **The `service_role` key bypasses all Row Level Security.** It belongs only in
> Vercel's server-side environment variables. Never put it in client code and
> never prefix it with `NEXT_PUBLIC_`.

---

## 2. Upstash QStash (~3 minutes)

1. Go to <https://console.upstash.com> → sign in with GitHub.
2. Top nav: **QStash**.
3. On the **Details** tab, copy all three values:
   - `QSTASH_TOKEN`
   - `QSTASH_CURRENT_SIGNING_KEY`
   - `QSTASH_NEXT_SIGNING_KEY`

The signing keys are what let the Python functions prove an incoming request
genuinely came from QStash — without them, anyone could hit `/api/generate` and
spend your OpenAI credit.

**Free tier limits that matter here:** 1,000 messages/day, max 10 concurrent
deliveries. A 100-post job uses ~100 analyze + up to 100 generate messages plus
a handful of scrape polls, so roughly **5 maxed-out jobs per day**.

---

## 3. Deploy to Vercel

### 3.1 Push to GitHub

```bash
gh repo create instagram-ai-generator --private --source=. --push
```

Or create an empty repo on github.com and then:

```bash
git remote add origin https://github.com/YOUR-USERNAME/YOUR-REPO.git && git push -u origin main
```

### 3.2 Import into Vercel

1. <https://vercel.com/new> → **Import** your repository.
2. Leave **Framework Preset** as **Next.js** and every build setting at its
   default. Vercel detects the Next.js app and the `/api/*.py` Python functions
   automatically.
3. Expand **Environment Variables** and add all of these before deploying:

| Name | Value |
|---|---|
| `NEXT_PUBLIC_SUPABASE_URL` | from step 1.5 |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | from step 1.5 |
| `SUPABASE_SERVICE_ROLE_KEY` | from step 1.5 |
| `NEXT_PUBLIC_SUPABASE_BUCKET` | `generated` |
| `QSTASH_TOKEN` | from step 2 |
| `QSTASH_CURRENT_SIGNING_KEY` | from step 2 |
| `QSTASH_NEXT_SIGNING_KEY` | from step 2 |
| `APIFY_TOKEN` | your Apify token |
| `OPENAI_API_KEY` | your OpenAI key |
| `IMAGE_QUALITY` | `medium` |

4. Click **Deploy**.

### 3.3 Set `PUBLIC_BASE_URL` and redeploy — required

QStash has to call your app back over the public internet, so the app must know
its own URL.

1. Once deployed, copy the production URL (e.g. `https://your-project.vercel.app`).
2. **Project → Settings → Environment Variables** → add:
   - `PUBLIC_BASE_URL` = `https://your-project.vercel.app` *(no trailing slash)*
3. **Deployments** → the latest one → **⋯** → **Redeploy**.

Without this, jobs are created but nothing ever processes them.

### 3.4 Confirm the Python functions are live — do this first

The single most important post-deploy check. Each Python function answers a
`GET` health probe that does no work and touches no paid API:

```bash
curl https://YOUR-PROJECT.vercel.app/api/analyze
```

Expected: `{"ok": true, "endpoint": "analyze", "method": "GET"}`

Check `/api/scrape`, `/api/scrape_poll`, and `/api/generate` too.

If you get a **404 or the Next.js error page** instead, Next.js is shadowing the
Python routes. Fix by adding a rewrite in `next.config.mjs`:

```js
async rewrites() {
  return { beforeFiles: [] }; // see README "Runtime coexistence" for the full workaround
}
```

...but check the health endpoints first — in a standard Next.js + `/api/*.py`
project this works out of the box.

---

## 4. First run

1. Open your Vercel URL. An anonymous session is created silently.
2. Paste a **single post URL** first (e.g. `instagram.com/p/SHORTCODE/`) — it
   costs one vision call and validates the whole chain cheaply.
3. Watch it reach **Ready for your review**, then press **Confirm & generate**.
4. When it completes, download the image and check the gradient starts at the
   midpoint of the `INSTAGRAM | FACTS4GENIUS` line.

### Validating single-post scraping

Apify's docs confirm `directUrls` accepts `/p/` and `/reel/` URLs with
`resultsType: "posts"`, but this hasn't been verified against live actor output.
The scraper's field extraction is deliberately tolerant, so if the actor names a
field differently in single-post mode it degrades rather than breaking. After
your first single-post run, check **Vercel → Logs** for `/api/scrape_poll` and
confirm likes/comments/caption/thumbnail all came through. If any are empty,
send me the raw dataset item and I'll pin the mapping exactly.

---

## 5. Optional: turn on the password gate

The app is fully open by default, by design. To require a password:

1. **Vercel → Settings → Environment Variables** → add `SITE_PASSWORD` = your
   chosen password.
2. Redeploy.

Visitors then enter it once and get a 30-day httpOnly cookie holding a SHA-256
digest (never the password itself). The QStash callback endpoints are exempt —
they're authenticated by request signature instead, and gating them would break
the pipeline.

To turn the gate off again, delete the variable and redeploy.

---

## 6. Local development (optional)

Python functions do **not** run under plain `next dev`. Use the Vercel CLI:

```bash
npm install -g vercel
```

```bash
cd "Instagram application" && vercel link && vercel env pull .env.local && npm run dev
```

`npm run dev` runs `vercel dev`, which serves both the Next.js app and the
Python functions. For UI-only work, `npm run dev:next` is faster but every
pipeline endpoint will 404.

QStash can't reach `localhost`, so a locally-created job won't process. Either
test against a preview deployment, or set `QSTASH_DEV=true` to use Upstash's
local dev server.
