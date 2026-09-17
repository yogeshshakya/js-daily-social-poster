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
2. Turns that research into an **8-slide carousel script** following a full
   narrative arc: title/hook -> the problem -> why it happens -> the fix ->
   how the fix works -> real-world impact -> a related pro tip ->
   takeaway/follow-CTA. The topic itself stays advanced and narrow, but the
   writing is instructed to explain it in **plain language** - short words, a
   quick analogy, and a one-clause translation the first time a technical
   term is used - and every content-slide panel also carries a one-line
   `plain` caption (no code, no jargon) rendered under the panel so a reader
   who isn't deep into engine/runtime internals still gets the point.
3. Renders each slide as a 1080x1350 image on a **deep-blue background**.
   The base texture is generated fresh each day by asking a Gemini
   image-generation model to produce an abstract navy/circuit-pattern
   texture in the style of `assets/bg_reference.jpg` (best-effort - see
   "AI background generation" below); the same hand-drawn circuit-line/node
   overlay is then drawn on top either way, so every slide still looks
   consistent even when AI generation isn't available. The 6 middle slides
   use a **4-panel infographic layout** with small connector-arrow icons
   between panels so the grid reads as one flow rather than four separate
   boxes, plus a small role icon (bug/warning/idea/clock/chart/star) next to
   each slide's heading; panel titles/content adapt to what each slide is
   actually showing (code input/output, step lists, scenario/impact, etc).
   The title/thumbnail slide is built as a proper cover: a red warning
   banner, an amber headline, the mascot (background auto-removed) posed and
   placed under a soft spotlight with a speech bubble beside his head (tail
   pointing at his face) where he talks to the viewer, a curved pointer
   leading down to **a visual that reflects today's actual topic** - either a
   real before/after code-diff card or a short step-by-step diagram of the
   underlying mechanism, whichever Gemini judges fits the topic (see "Cover
   visual: code-diff or diagram, chosen per topic" below) - and an "ADVANCED"
   difficulty badge. **The mascot side/pose also rotates daily** so
   consecutive posts don't look like the same template - see "Cover layout
   rotation" below. Only that slide has the mascot, as requested.
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

### Slide rendering: AI-generated slides, with a procedural safety net

`SLIDE_IMAGE_MODE` (env var / optional repo secret) controls how each slide
is drawn:

- **`ai` (default)** - the image model is handed `assets/bg_reference.jpg`
  (and, for the cover slide, `assets/avatar.png`) plus the exact text that
  must appear, and asked to lay out the whole slide itself: same reference
  background, infographic panels, icons, flow arrows. This gives much richer
  visuals than the procedural renderer can.
- **`procedural`** - skip AI entirely and draw every slide with PIL, which
  always works and renders text perfectly.

Important honest caveat: image-generation models are **not reliable at
rendering exact text**, especially code. Some slides may come back with
misspelled words, garbled code, or invented text. Every slide therefore falls
back to the procedural renderer automatically if generation fails, and the
logs show which path each slide took (`AI slide 3/8: generated successfully
with '<model>'` vs `AI slide 3/8: falling back to the procedural renderer`).
If you find the AI slides' text too unreliable in practice, set the
`SLIDE_IMAGE_MODE` secret to `procedural` - no code change needed.

Note this makes 8 image-generation calls per run instead of 1, so it uses
noticeably more of your Gemini quota than the background-only mode did.

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

This affects both the AI-generated cover (the prompt tells the image model
which side the mascot stands on and which pose it's given) and the
procedural PIL fallback (which mirrors the whole layout - avatar, speech
bubble, and the visual it points at - left or right). To add more variety,
add entries to `COVER_VARIANTS`.

### Cover visual: code-diff or diagram, chosen per topic

The thing the mascot points to on the cover is no longer a generic
"before/after" label - it's built from the actual topic. In `build_carousel`'s
prompt, Gemini fills a `cover_visual` object and picks its own `style`:

- **`code`** - for topics where the bug/fix genuinely is one line of code.
  Renders as a small code-diff card: a red "Before" line with the real buggy
  code, an arrow, a green "After" line with the real fix
  (`draw_cover_code_diff()`).
- **`diagram`** - for topics that are more about an internal mechanism than
  one code line. Renders as 2-4 connected step chips describing what
  actually happens (`draw_cover_diagram()`), e.g. "Object has a shape" →
  "delete removes a slot" → "Shape invalidated" → "Falls to dict mode".

Gemini is told to pick whichever genuinely fits that day's topic rather than
defaulting to one every time. If a response is missing both `code_before`
and `diagram_steps` (e.g. an older cached script, or a partial response),
`draw_cover_visual()` falls back to the original two-card before/after
layout so nothing breaks.

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

### AI background generation (best-effort)

Each run asks a Gemini **image-generation** model (`generate_daily_background()`
in `scripts/generate.py`) to create today's base background texture, using
`assets/bg_reference.jpg` as a style reference. This is genuinely
best-effort:

- It tries several candidate model names in order (`gemini-2.5-flash-image`,
  `gemini-3-pro-image`, and a couple of older preview names), the same
  fallback pattern used for the text-generation models.
- Not every Google account/API key has access to an image-generation model
  yet (this is a newer, still-rolling-out capability, similar to how Search
  grounding turned out to need a paid tier). If none of the candidates work,
  the script **automatically falls back** to the original procedural
  gradient + hand-drawn circuit pattern - posting still succeeds, it just
  uses the procedural background instead of an AI one that day.
- Check the Action run's logs for lines starting with `AI background:` to
  see whether it succeeded (`generated successfully with '<model>'`) or
  fell back (`all candidate models failed, using procedural gradient
  instead`).
- You can pin a specific image model with an optional `IMAGE_MODEL` repo
  secret (same idea as `GEMINI_MODEL`). Run the **"Check setup"** workflow
  (see below) to see which image-generation models, if any, your API key
  currently has access to.

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
