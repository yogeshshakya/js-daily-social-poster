# Daily JS/React/Next.js Poster

Every day at 7:00 PM IST, this repo's GitHub Actions workflow:

1. Picks a JavaScript/React/Next.js topic and asks Gemini to write a title,
   short explanation, optional code snippet, and a caption.
2. Renders a 1080x1080 image card from that content.
3. Commits the image into `images/`.
4. Posts the image + caption to your Telegram channel and your Instagram
   Business account.

It runs entirely on GitHub's servers, so it does not depend on your laptop
being on.

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
- The generated image for each day is committed to `images/` in this repo
  (that's also how Instagram fetches it - via its public raw GitHub URL).
  Feel free to periodically delete old ones if the repo grows too large.
- To change the posting time, edit the `cron` line in
  `.github/workflows/daily-post.yml` (times are in UTC).
- To change the topic pool or writing style, edit `scripts/topics.py` and
  the prompt in `scripts/generate.py`.
