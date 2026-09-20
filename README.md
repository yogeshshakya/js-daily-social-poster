# Daily JS/React/Next.js Poster

Every day at 7:00 AM IST, this repo's GitHub Actions workflow:

1. Asks Gemini to **pick today's own topic** (`choose_topic()` in
   `scripts/generate.py`) within four subjects - JS latest features, JS
   performance optimization, React internals/best practices, Next.js
   internals/best practices - with instructions to keep it **advanced and
   commonly-misunderstood** (things experienced developers actually get
   wrong, not textbook basics), and to avoid repeating anything in
   `data/topic_history.json` (the last ~30 topics posted, tracked
   automatically). `scripts/topics.py` is kept only as a style-calibration
   reference and as a **fallback pool** if the topic-selection call itself
   fails for any reason. Gemini then **researches** the chosen topic
   (written from the model's own knowledge; Google Search grounding is not
   used since it isn't available on free-tier API keys - see the note in
   `scripts/generate.py`'s `research_topic()`).
2. Turns that research into an **8-slide carousel script** using a
   user-authored prompt (`CAROUSEL_PROMPT_TEMPLATE` in `scripts/generate.py`,
   used verbatim - only `{topic}`/`{research}` are substituted) that follows a
   fixed narrative arc, one fixed `type` per slide: `hook` -> `simple_explanation`
   -> `code_example` -> `flow` -> `under_the_hood` -> `common_mistake` ->
   `better_approach` -> `summary`. Each slide comes back as a flat object -
   `title`, `content`, `code`, `visual_type`, `visual_story`, `infographic`,
   `highlight`, `design_emphasis` - instead of the old nested panel/heading
   structure. The topic itself stays advanced and narrow, but the writing is
   instructed to explain it in **plain language** - short sentences, small
   analogies, and a one-clause translation the first time a technical term is
   used. This step (the TEXT/script content) is told **not** to write the
   mascot into the slide content itself - the avatar's actual visual
   appearance is a separate concern, handled entirely by the image-generation
   prompts in step 3 below.
3. Renders each slide as a 1080x1350 image on a **deep-blue background**,
   with the mascot appearing on **every slide, not just the cover** - each of
   the 8 slides is built from its own **user-authored image-generation
   prompt**, used verbatim:
   - **Slide 1 (hook/thumbnail)**: `THUMBNAIL_PROMPT_TEMPLATE` (see
     "Thumbnail: a user-authored, ever-changing prompt" below) - a cover
     treatment where the avatar is an active storytelling element.
   - **Slides 2-8 (body slides)**: `BODY_SLIDE_PROMPT_TEMPLATE` - picks one
     visual composition per slide (code+callouts, before/after,
     flow diagram, comparison, etc, chosen by content) and explicitly calls
     for the avatar to appear as a "visual teacher/presenter" with a pose
     that varies slide to slide and is never repeated, not just standing
     passively beside the content.
   Both templates are handed `assets/bg_reference.jpg` (as a *brand-style*
   reference, not to be copied exactly - each slide gets a fresh background
   treatment) and `assets/avatar.png` (the mascot, character design
   preserved, pose/placement free to vary) - see `build_thumbnail_prompt()`
   and `build_body_slide_prompt()` in `scripts/generate.py` for the exact
   field mapping from the new flat schema into each template's placeholders.
   The base background texture used for the *procedural* fallback path is
   generated fresh each day too (best-effort - see "AI background
   generation" below); the same hand-drawn circuit-line/node overlay is then
   drawn on top either way, so a procedurally-rendered slide still looks
   consistent even when AI generation isn't available. The procedural PIL
   fallback (used only when `SLIDE_IMAGE_MODE=procedural`, or previously as
   an automatic fallback - see "Slide rendering" below for why that's no
   longer automatic) is simpler: it draws the mascot only on the hook slide
   (a fixed layout can't reproduce the AI prompts' per-slide creative
   variation), and the 6 middle body slides use a full-width text/code/
   highlight layout with a small role icon instead - see "Slide rendering"
   below for exactly what it draws. The hook slide's procedural fallback
   still draws a red warning banner (from `highlight`), an amber headline
   (from `title`), the mascot under a soft spotlight with a speech bubble
   (from `content`), and an "ADVANCED" difficulty badge; **the mascot
   side/pose also rotates daily** in that fallback so consecutive posts
   don't look like the same template - see "Cover layout rotation" below.
4. Writes an Instagram caption as **short scannable bullet points** instead
   of a paragraph: one hook line, 3-4 plain-language bullet points that on
   their own explain the concept, and a one-line takeaway/CTA - followed by
   2-3 evergreen SEO keyword phrases and a curated mix of
   broad/niche/community/branded hashtags (10-15 tags). A local safety net
   (`_normalize_caption_parts()`) guarantees this shape even if Gemini
   ignores the instruction and returns a run-on paragraph in one field - see
   "Caption: guaranteed bullet points" below. The bullet body is
   separated from the keywords and hashtags by a forced blank-line gap
   (Instagram normally collapses plain blank lines, so this uses invisible
   Braille-blank characters to keep the spacing visible), plus a shorter
   Telegram-safe variant.
5. Commits the images into `images/`, and appends today's topic to
   `data/topic_history.json` (used to avoid repeats - see below).
6. Posts the full carousel + caption to your Telegram channel (as an album)
   and your Instagram Business account (as a carousel post).

**Note:** the GitHub repo needs to be **public** for this to work - Instagram
fetches each image from its public `raw.githubusercontent.com` URL, and a
private repo's raw URLs require authentication that Instagram can't provide.

It runs entirely on GitHub's servers, so it does not depend on your laptop
being on. To change the tone, topic pool, slide count, color theme, or
hashtag mix, edit `scripts/topics.py` and the prompt/theme constants in
`scripts/generate.py`. To change the mascot, replace `assets/avatar.png`
with any image that has a plain white/light background (it gets silhouetted
automatically). To change the background style reference, replace
`assets/bg_reference.jpg` with a different image.

### Posting to a Facebook Page (optional)

Telegram and Instagram post by default. To also post to a Facebook Page, set
the `FB_PAGE_ID` and `FB_PAGE_TOKEN` secrets; if either is missing the
Facebook step is skipped quietly and nothing else changes.

Facebook has no carousel object for Page posts. The equivalent is a
multi-photo post, so `post_to_facebook()` uploads each slide to
`/{page-id}/photos` with `published=false`, then attaches all of the returned
`media_fbid`s to one `/{page-id}/feed` post carrying the Instagram caption.
The result is a single post with all 8 slides in it.

Two things this needs that Instagram posting does not:

1. **A Page access token, not your user token.** Get it from Graph API
   Explorer: select your app, generate a user token with the
   `pages_manage_posts` and `pages_read_engagement` permissions, then switch
   the token dropdown from your user to the Page itself, generate again, and
   copy that value into `FB_PAGE_TOKEN`. A Page token derived from a
   long-lived user token is long-lived too.
2. **The `pages_manage_posts` permission.** Without it the upload fails with
   a permissions error in the Action log; nothing else breaks.

Run the **"Check setup"** workflow after adding the secrets - it now reports
whether both are present, what the token's type and scopes are, and when it
expires.

**If you see `Object with ID '***' does not exist` (code 100, subcode 33)**
for Facebook, the page id and the token don't match up. The masked `***` is
your `FB_PAGE_ID` secret, and Facebook is saying that object isn't visible to
that token. `post_to_facebook()` now checks this before uploading anything
and prints which of these it is: the id isn't the numeric Page ID, the token
is a USER token rather than that Page's own token, or the token lacks
`pages_manage_posts`. If the token itself knows which Page it belongs to, the
script uses that id automatically and tells you the correct value to put in
`FB_PAGE_ID`.

### Topic selection (Gemini picks it, not a fixed list)

`choose_topic()` in `scripts/generate.py` asks Gemini to pick a specific,
advanced, narrow topic each day within four subjects (JS latest features,
JS performance optimization, React best practices, Next.js best practices).
It's given `scripts/topics.py`'s entries only as style/depth examples (how
narrow and advanced the topic should be), not as the pool it must choose
from - so topics can be genuinely new day to day, not limited to 20 fixed
entries.

The prompt asks it to pick between three flavours: (a) a
commonly-misunderstood behavior or bug, (b) something new or recently
changed in the ecosystem that experienced developers still get wrong, and
(c) the advanced concept underneath whatever is genuinely trending today in
the JS/React/Next.js community, for growth. Since Google Search grounding
isn't used (paid-tier only), flavour (b) comes from the model's own training
knowledge rather than live news, so the prompt explicitly tells it to fall
back to flavour (a) rather than guess about any release or API it isn't
confident about.

**Flavour (c) is grounded in real data, not a guess.** Before asking Gemini
to pick, `fetch_trending_signals()` pulls today's actual top discussion
titles from two free, no-key-needed public APIs - Hacker News (via the
Algolia search API, filtered to JS/React/Next.js/TypeScript/V8 stories) and
Reddit's `r/javascript`, `r/reactjs`, `r/nextjs` top-of-day - and hands those
titles to Gemini as live context: "here's what's actually being discussed
today, find the advanced concept underneath it." This is best-effort like
everything else network-dependent here: if both sources fail (rate-limited,
blocked, or just down), `choose_topic()` proceeds without them and falls
back to flavours (a)/(b) - check the logs for `Trending signal: ... fetch
failed` to see if that happened on a given run. (In this repo's own dev
sandbox both APIs are blocked by the sandbox's proxy, which is expected -
GitHub Actions has open internet, so this works there.)

To avoid repeats, every topic actually posted is appended to
`data/topic_history.json` (kept to the last ~30), and that list is sent back
to Gemini each day with instructions to pick something meaningfully
different. If the topic-selection call itself fails (network/quota issues),
the script falls back to randomly picking from the local `scripts/topics.py`
pool instead, so a post never fails purely because of this step - check the
logs for `Gemini topic selection failed (...); falling back to local topic
pool` to see if that happened.

### Slide rendering: AI-generated slides, no silent fallback

`SLIDE_IMAGE_MODE` (env var / optional repo secret) controls how each slide
is drawn:

- **`ai` (default)** - the image model is handed `assets/bg_reference.jpg`
  and `assets/avatar.png`, plus the full user-authored prompt for that
  slide - `THUMBNAIL_PROMPT_TEMPLATE` for the hook/cover slide,
  `BODY_SLIDE_PROMPT_TEMPLATE` for the other 7 - and asked to lay out the
  whole slide itself. This is the only mode that actually uses either
  prompt's creative instructions (rotating thumbnail direction,
  avatar-as-storyteller/presenter on every slide, etc) - the procedural
  renderer below is a fixed template and cannot reproduce that.
- **`procedural`** - skip AI entirely and draw every slide with PIL, which
  always works and renders text perfectly, but is a fixed layout (does not
  use `THUMBNAIL_PROMPT_TEMPLATE`'s creative-rotation instructions).

**If AI generation fails in `ai` mode, the run now fails loudly instead of
silently switching to the procedural renderer.** Earlier versions of this
script fell back automatically, which meant a quota/billing/dead-model
problem could go unnoticed for weeks while every post quietly used the
plain procedural layout instead of the AI-generated one. This applies to
**every slide, not just the cover** - the background and all 8 slides each
raise on their own if every candidate model fails for them. Check the
`AI background: model '<model>' failed with <code>: ...` / `AI slide N/8:
model '<model>' failed with <code>: ...` lines directly above the error for
the exact cause. The two most common ones:

- **429 "You exceeded your current quota"** - your Google AI Studio account's
  image-generation quota/billing is the problem, not the code. Check
  https://ai.dev/rate-limit and your plan/billing at aistudio.google.com.
- **404 "models/... is not found for API version v1beta"** - that model name
  no longer exists/was renamed. Check
  https://ai.google.dev/gemini-api/docs/models for current image-model names
  and update `IMAGE_MODEL_CANDIDATES` in `scripts/generate.py` (or set the
  `IMAGE_MODEL` secret to pin a specific one you've confirmed works).

If you deliberately want the procedural renderer (e.g. while sorting out a
quota issue), set the `SLIDE_IMAGE_MODE` secret to `procedural` - that mode
still never fails the run over image generation, exactly as before.

Note `ai` mode makes 8 image-generation calls per run instead of 1, so it
uses noticeably more of your Gemini quota than the background-only mode did.

**All 8 slides use the mascot, not just the hook.** Both `assets/bg_reference.jpg`
and `assets/avatar.png` are attached to every AI image-generation call, hook
and body slides alike, and `BODY_SLIDE_PROMPT_TEMPLATE` explicitly calls for
the avatar to appear as a visual teacher/presenter on each of the 7 body
slides, with a pose/placement that varies slide to slide and is never
repeated. The script/content-generation step (step 2 above) still doesn't
write the mascot into the slide's textual content - that's a separate,
text-only concern - but visually, the avatar now shows up everywhere.
The *procedural* PIL fallback (`SLIDE_IMAGE_MODE=procedural` only) is the
one place that's still hook-only: a fixed procedural layout can't reproduce
the AI prompts' per-slide creative variation, so its 6 middle body slides
use a full-width text/code/highlight layout with a small role icon instead
of the mascot - see below for exactly what it draws.

### Cover layout rotation

To stop every day's thumbnail looking like the same template with different
text, `choose_cover_variant()` in `scripts/generate.py` picks one of 4
mascot side/pose combinations each day, avoiding whatever was used the
previous run (tracked in `data/topic_history.json` alongside the topic):

| variant id | mascot side | pose |
|---|---|---|
| `right_pointing` | right | pointing |
| `left_excited` | left | excited |
| `right_thinking` | right | thinking |
| `left_pointing` | left | pointing |

This affects both the AI-generated cover (the user-authored thumbnail prompt
tells the image model which side the mascot stands on and which pose it's
given, since that's filled in from the same `variant`) and the procedural PIL
fallback (which mirrors the avatar + speech bubble left or right). To add
more variety, add entries to `COVER_VARIANTS`.

### Thumbnail and body slides: two user-authored, verbatim prompts

Every AI-generated slide image comes from one of two long, detailed,
user-authored prompts in `scripts/generate.py`, each used **verbatim** -
only their bracketed placeholders are filled in per slide, and the wording
around those placeholders is never touched by the code.

**Hook/thumbnail slide (`THUMBNAIL_PROMPT_TEMPLATE`, filled in by
`build_thumbnail_prompt()`).** Its core instruction is that **every
thumbnail must look meaningfully different** from the last one: a different
creative direction (cinematic, investigation, before/after, futuristic UI,
comic/reaction, 3D, breaking-news, minimal editorial, visual metaphor,
debugging scenario - the model picks whichever fits that day's topic), a
different avatar pose/placement each time, and a fresh (but on-brand)
background - while keeping the mascot's actual character design identical to
`assets/avatar.png`. The six text fields it fills in (topic, label, hook,
headline, subtitle, avatar speech) are derived from the new flat schema's
`title`/`content`/`highlight` fields on the hook slide (see
`build_thumbnail_prompt()` for the exact mapping), since the old
`kicker`/`alert`/`subtitle`/`avatar_line` fields no longer exist.

**Body slides 2-8 (`BODY_SLIDE_PROMPT_TEMPLATE`, filled in by
`build_body_slide_prompt()`).** A separate, ~400-line prompt for the 7
non-hook slides, picking one visual composition per slide (code + callouts,
before/after, flow diagram, comparison, decision tree, etc, chosen to fit
that slide's content) and calling for the avatar to appear on every one of
them too, as a visual teacher/presenter whose pose and placement vary slide
to slide and never repeat. `build_body_slide_prompt()` maps the flat
schema's fields onto this template's placeholders: carousel topic, slide
number ("N of 8"), the slide's purpose (from `visual_story`, falling back to
a label derived from `type`), `title`, `content`, `code` (or `(none)`),
`highlight`/`infographic` as callouts, and a short avatar line derived from
`highlight` or the first sentence of `content`.

### Caption: guaranteed bullet points

Gemini doesn't reliably keep `caption_hook`/`caption_points` short and
separate on its own - it can dump a whole explanation into one field as a
run-on paragraph even when the prompt asks for short bullets. Rather than
trust the model's formatting, `_normalize_caption_parts()` in
`scripts/generate.py` enforces the shape locally before the caption is ever
written to a file: any field that turns out to be multiple sentences gets
split on sentence boundaries, and any single very long sentence (>22 words)
gets split on its natural clause breaks. The result is always genuinely
bulleted, regardless of what shape Gemini's response came back in. `dry_run.py`
includes a test that feeds a deliberately messy single-paragraph string
through this and prints the result, so you can verify it before trusting it
in production.

### SEO keywords & hashtags: explicit prompt guidance, required not optional

`CAROUSEL_PROMPT_TEMPLATE`'s "CAPTION, SEO & HASHTAGS" section spells out
exactly what makes a good `seo_keywords`/`hashtags` response instead of
leaving Gemini to guess from the bare JSON schema:

- **`seo_keywords`** (3-5 entries): real search-phrase quality, not generic
  filler - at least 1-2 should be specific to that day's exact mechanism/bug
  (e.g. "v8 hidden classes explained"), not just broad terms every post could
  reuse.
- **`hashtags`** (10-15 entries): a deliberate 3-tier mix each time - 3-4
  broad/high-volume tags for reach, 5-7 tags niche to that day's specific
  technology/topic (not a fixed list reused daily), 1-2 community/branded
  tags.

`build_carousel()` now **requires** both to be non-empty and raises if
Gemini returns either as an empty array - previously they were silently
defaulted to `[]`, so a run could succeed with zero hashtags/keywords and
nobody would notice. `build_captions()` renders `seo_keywords` as a bracketed
`[kw1, kw2, ...]` line and joins `hashtags` (capped at 15 for Instagram, 5 for
Telegram) - both appear after the bullet body, separated by a forced blank
line (see the `⠀` Braille-blank trick below).

### Mascot poses (generated once, then cached)

The bundled `assets/avatar.png` is a single static image, so his pose and
expression can't be changed in code. Instead, `get_avatar_pose()` asks the
image model to redraw that same character in a given pose - `pointing`,
`excited`, and `thinking` are all in active use now, one per cover variant
above (previously only `pointing` was actually used even though all three
were generated) - keeping the same face, clothes and art style, on a plain
white background that the existing cutout code silhouettes.

The result is written to `assets/avatar_poses/<pose>.png` and committed by
the workflow, so **each pose is generated only once** - every later run just
loads the cached file. If image generation isn't available on your account,
the code silently falls back to the original `assets/avatar.png` and logs
`Avatar pose '<pose>': unavailable, using the original avatar.`

**If the generated character doesn't look like your mascot** (identity drift
is the common failure of image-to-image editing), you have three options, in
order of reliability:

1. **Supply the pose yourself** - put your own PNG at
   `assets/avatar_poses/pointing.png` (plain white background, full body).
   A file that already exists is never regenerated, so this always wins.
2. **Re-roll** - delete `assets/avatar_poses/pointing.png` and re-run the
   workflow; each generation is a fresh draw, so a second or third try often
   lands much closer.
3. **Turn it off** - set the `AVATAR_POSE_MODE` repo secret to `off` and the
   original `assets/avatar.png` is used exactly as supplied, every time.

The prompt already pins the character down in words as well as with the
image (`AVATAR_IDENTITY` in `scripts/generate.py`) and asks for the smallest
possible edit, which helps a lot but cannot fully guarantee consistency. If
you swap in a different mascot, update that description too - or override it
with an `AVATAR_DESCRIPTION` secret.

### AI background generation

Each run asks a Gemini **image-generation** model (`generate_daily_background()`
in `scripts/generate.py`) to create today's base background texture, using
`assets/bg_reference.jpg` as a style reference.

- It tries the candidate model names in `IMAGE_MODEL_CANDIDATES` in order
  (`gemini-2.5-flash-image`, `gemini-3-pro-image`), the same fallback
  pattern used for the text-generation models.
- **In `SLIDE_IMAGE_MODE=ai` (the default), if every candidate fails, the run
  now fails loudly** instead of silently using the procedural gradient - see
  "Slide rendering: AI-generated slides, no silent fallback" above for why,
  and for how to read the `AI background: model '<model>' failed with
  <code>: ...` lines to find the actual cause (quota/billing is the most
  common one). In `SLIDE_IMAGE_MODE=procedural`, a failure here still falls
  back to the procedural gradient quietly, since that mode was already opted
  out of AI generation.
- You can pin a specific image model with an optional `IMAGE_MODEL` repo
  secret (same idea as `GEMINI_MODEL`). Run the **"Check setup"** workflow
  (see below) to see which image-generation models, if any, your API key
  currently has access to.

### Image-request throttling (avoiding 429s)

A single `ai`-mode run makes up to ~9 separate image-generation calls
(1 background + 8 slides), each of which can try up to 2 candidate models -
so up to ~18 requests, with nothing pacing them. Fired back-to-back with no
gap, that alone can trip Google's **per-minute** rate limit even when your
per-day quota is fine, which shows up as a 429 "You exceeded your current
quota" error (see "Slide rendering" above).

`_throttle_image_request()` in `scripts/generate.py` now enforces a minimum
gap between any two outgoing image requests: before each one, it waits
until at least `IMAGE_REQUEST_MIN_GAP_SECONDS` (default **8 seconds**) have
passed since the previous request finished. This applies uniformly to the
background call, every slide, and every candidate-model retry within a
call - it's a simple sequential queue, not real concurrency, so requests
never actually overlap.

- Override the gap with the `IMAGE_REQUEST_MIN_GAP_SECONDS` env var / repo
  secret (e.g. `15` if you're still hitting 429s, or `0` to disable it
  entirely and go back to unthrottled back-to-back requests).
- This only spaces out requests within a single run - it does not change
  your account's actual rate limit or quota, and does not retry a request
  that already failed with 429 (see "Slide rendering" above for what to do
  when a run still fails after this).
- Watch for `Image request throttle: waiting Ns before the next
  image-generation call.` in the logs to see it in action.

## One-time setup

### 1. Create the repo
Create a new **private** GitHub repo (e.g. `js-daily-social-poster`) under
your account and push these files to it (`main` branch).

### 2. Add repository secrets
Go to your repo -> **Settings -> Secrets and variables -> Actions -> New
repository secret**, and add each of these:

| Secret name | Value |
|---|---|
| `GEMINI_API_KEY` | your Gemini API key from aistudio.google.com |
| `TELEGRAM_BOT_TOKEN` | your bot token from @BotFather |
| `TELEGRAM_CHAT` | `@modernjavascripthub` (or your channel's numeric id) |
| `IG_ACCESS_TOKEN` | your long-lived Instagram Graph API access token |
| `IG_BUSINESS_ID` | see step 3 below - must be the **numeric** ID |
| `GEMINI_MODEL` *(optional)* | pin a specific text-generation model instead of the automatic fallback list |
| `IMAGE_MODEL` *(optional)* | pin a specific image-generation model instead of the automatic fallback list |
| `SLIDE_IMAGE_MODE` *(optional)* | `ai` (default) or `procedural` - see "Slide rendering" below |
| `IMAGE_REQUEST_MIN_GAP_SECONDS` *(optional)* | minimum seconds between image-generation requests, default `8` - see "Image-request throttling" below |
| `FB_PAGE_ID` *(optional)* | numeric Facebook Page ID - set with the next one to also post to Facebook |
| `FB_PAGE_TOKEN` *(optional)* | that Page's own access token (not your user token) |

### 3. Find your real numeric Instagram Business Account ID
Go to the **Actions** tab -> **"Check setup (Telegram + Instagram
diagnostics)"** workflow -> **Run workflow**. Open the run and check the
logs:

- The "Telegram" section confirms your bot token works and can see your
  channel.
- The "Instagram" section lists your Facebook Pages and, for each one, the
  linked `instagram_business_account.id` - a numeric value like
  `17841400123456789`. Copy that number and update the `IG_BUSINESS_ID`
  secret with it (replace the placeholder username value).

Re-run this workflow any time you want to sanity-check your secrets without
actually posting anything.

### About the posting time (GitHub's scheduler is best-effort)

The workflow targets **7:00 AM IST**, but GitHub does not guarantee that a
scheduled run starts on time. Its own docs say scheduled events "can be
delayed during periods of high loads of GitHub Actions workflow runs", that
"high load times include the start of every hour", and that "if the load is
sufficiently high enough, some queued jobs may be dropped". Delays of a few
hours, and occasionally a whole skipped day, are normal on the free tier.

Two things in this repo reduce the damage:

- **Odd minutes, three attempts.** Instead of one cron at a busy slot, there
  are three (06:56, 07:14 and 07:42 IST) on unpopular minutes. If one is
  dropped or badly delayed, a later one usually gets through.
- **A once-a-day guard.** The first step reads `data/last_post.json`; if it
  already records today's date (IST), the run exits immediately without
  posting. The date is written only after posting actually succeeds, so a
  failed run doesn't block the next attempt. Manual runs
  (`workflow_dispatch`) always proceed, guard or not.

If you need the post to go out at an exact minute, GitHub's own scheduler
cannot give you that at any setting. The reliable pattern is to trigger the
workflow from outside: keep only `workflow_dispatch` here, and have a free
external scheduler (cron-job.org, a Cloudflare Worker cron, Google Cloud
Scheduler, or any always-on machine) call the REST endpoint
`POST /repos/{owner}/{repo}/actions/workflows/daily-post.yml/dispatches` at
the time you want, using a fine-grained token with Actions write access on
this repo.

### 4. Enable the daily schedule
Nothing else to do - the `daily-post.yml` workflow is already scheduled for
01:30 UTC (7:00 AM IST) every day once the file is on the `main` branch.
GitHub disables scheduled workflows on repos with no activity for 60+ days,
so if it stops firing after a long pause, just push any small commit or
re-enable it from the Actions tab.

You can also trigger a post immediately (to test end-to-end) from **Actions
-> "Daily JS/React/Next.js post" -> Run workflow**.

## Important notes

- **Instagram access tokens expire.** A long-lived token lasts ~60 days.
  Before it expires, generate a new one (Graph API Explorer -> exchange for
  long-lived token, same steps as initial setup) and update the
  `IG_ACCESS_TOKEN` secret. Instagram will simply fail with an
  authentication error in the Action logs when it expires - check the
  Actions tab occasionally.
- **Instagram Content Publishing has a rate limit** (25 posts/24h per
  account), so one post a day is well within limits.
- The generated images for each day (one per carousel slide) are committed
  to `images/` in this repo (that's also how Instagram fetches them - via
  their public raw GitHub URLs). Feel free to periodically delete old ones
  if the repo grows too large.
- To change the posting time, edit the `cron` line in
  `.github/workflows/daily-post.yml` (times are in UTC).
- To change the topic pool or writing style, edit `scripts/topics.py` and
  the prompt in `scripts/generate.py`.
