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
   takeaway/follow-CTA.
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
   banner, an amber headline, the bundled mascot (`assets/avatar.png`,
   background auto-removed) standing on the right under a soft spotlight
   with a speech bubble beside his head (tail pointing at his face) where he
   talks to the viewer, a curved pointer leading down to a red/green
   before-after comparison of the wrong vs right approach, and an "ADVANCED"
   difficulty badge. Only that slide has the mascot, as requested.
4. Writes an SEO-style Instagram caption (hook + value + 2-3 evergreen
   high-search-volume keyword phrases + CTA) with a curated mix of
   broad/niche/community/branded hashtags (10-15 tags), separated from the
   caption body by a forced blank-line gap (Instagram normally collapses
   plain blank lines, so this uses invisible Braille-blank characters to
   keep the spacing visible), plus a shorter Telegram-safe variant.
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

### Topic selection (Gemini picks it, not a fixed list)

`choose_topic()` in `scripts/generate.py` asks Gemini to pick a specific,
advanced, narrow topic each day within four subjects (JS latest features,
JS performance optimization, React best practices, Next.js best practices).
It's given `scripts/topics.py`'s entries only as style/depth examples (how
narrow and advanced the topic should be), not as the pool it must choose
from - so topics can be genuinely new day to day, not limited to 20 fixed
entries.

The prompt asks it to alternate between two flavours: (a) a
commonly-misunderstood behavior or bug, and (b) something new or recently
changed in the ecosystem that experienced developers still get wrong.
Since Google Search grounding isn't used (paid-tier only), flavour (b) comes
from the model's own training knowledge rather than live news, so the prompt
explicitly tells it to fall back to flavour (a) rather than guess about any
release or API it isn't confident about.

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

### Mascot poses (generated once, then cached)

The bundled `assets/avatar.png` is a single static image, so his pose and
expression can't be changed in code. Instead, `get_avatar_pose()` asks the
image model to redraw that same character in a given pose (currently
`pointing`, used on the cover so he looks like he is presenting the
comparison cards, plus `excited` and `thinking` defined for future use),
keeping the same face, clothes and art style, on a plain white background
that the existing cutout code silhouettes.

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
