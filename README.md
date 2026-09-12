# Daily JS/React/Next.js Poster

Every day at 7:00 PM IST, this repo's GitHub Actions workflow:

1. Picks an **advanced, commonly-misunderstood** JS/React/Next.js topic
   (see `scripts/topics.py`) - things experienced developers actually get
   wrong, not textbook basics - and asks Gemini to **research it** (grounded
   with Google Search, so facts stay current and accurate).
2. Turns that research into a **4-slide carousel script**: a title/hook
   slide, a "the buggy version" slide, a "the fix" slide, and a
   takeaway/follow-CTA slide.
3. Renders each slide as a 1080x1350 image on a **deep-blue gradient with a
   circuit-pattern background**. The two middle slides use a **4-panel
   infographic layout** (Code Input / Execution Flow / Console Output /
   Internal Mechanics), matching a "before vs after" explainer format. The
   title/thumbnail slide uses the bundled mascot (`assets/avatar.png`, with
   its background auto-removed) - only that slide, as requested.
4. Writes an SEO-style Instagram caption (hook + value + CTA) with a curated
   mix of broad/niche/branded hashtags, plus a shorter Telegram-safe variant.
5. Commits the images into `images/`.
6. Posts the full carousel + caption to your Telegram channel (as an album)
   and your Instagram Business account (as a carousel post).

It runs entirely on GitHub's servers, so it does not depend on your laptop
being on. To change the tone, topic pool, slide count, color theme, or
hashtag mix, edit `scripts/topics.py` and the prompt/theme constants in
`scripts/generate.py`. To change the mascot, replace `assets/avatar.png`
with any image that has a plain white/light background (it gets silhouetted
automatically).

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
13:30 UTC (7:00 PM IST) every day once the file is on the `main` branch.
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
