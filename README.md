# The Build Sheet

Weekly newsletter pipeline for [FounderCloud](https://www.foundercloud.com/). Every Monday it renders an issue from the Sep 12, 2026 layout, publishes the HTML on GitHub Pages, and — once Zoho credentials exist — creates an **unsent** draft in Zoho Campaigns (EU). Review, edit, and press Send in Zoho. This repo never calls `sendcampaign`.

## What a run does

1. Pulls the latest approved blog posts from `https://api.starthawk.io/` (`blogArticles`, `orderBy: "date_DESC"`), preferring slugs not already listed in `state/featured.json`.
2. Picks 3–4 recent public founder profiles (`usersLookingForCofounder`) that have a name, location, Cloudinary photo, at least one skill, and a pitch, and that have not been featured before. Email addresses written into a pitch are removed. Profiles whose public text recruits minors are skipped.
3. Reads the community count from `usersLookingForCofounderConnection` and shows it floored to the nearest 500 with a plus (`32,949` → `32,500+`). The exact count is in an HTML comment and in the state file.
4. Fills the hook, quote, subject, and preheader from `content/YYYY-MM-DD.yml` when that file exists. Otherwise those spots are marked `[EDIT ME]`.
5. Writes `issues/YYYY-MM-DD.html` and `issues/latest.html`, commits them with the state file, and deploys the `issues/` folder with GitHub Pages.
6. If the Zoho secrets below are all set, waits until the Pages URL returns 200, then `POST`s a form body to `https://campaigns.zoho.eu/api/v1.1/createCampaign` and checks the draft with `getcampaigndetails`. The campaign key is printed in the job log and saved on the issue in `state/featured.json`.

## Trigger

- **Schedule:** Mondays at 13:00 UTC (about 9:00am US Eastern). GitHub may start the job a few minutes late.
- **Manual:** Actions → Build Sheet → Run workflow. Optional input `date` (`YYYY-MM-DD`, default today UTC). Run it from `main`.

The workflow file is `.github/workflows/newsletter.yml`. A push from `GITHUB_TOKEN` does not start another run.

## One-time setup

### GitHub Pages

Pages source cannot be set from this repo. After this workflow is on `main`:

1. Open **Settings → Pages**.
2. Under **Build and deployment**, set **Source** to **GitHub Actions**.
3. Save.

The first deploy uses the `github-pages` environment. If GitHub asks you to approve it, approve that environment once. Then run the workflow (or wait for Monday). Until that setting is saved, the render-and-commit job can still succeed and the deploy job will fail.

### Zoho Campaigns (EU)

Create an EU server-based OAuth client (Zoho API Console, data center **EU**) with scopes that can create and read campaigns, for example `ZohoCampaigns.campaign.CREATE` and `ZohoCampaigns.campaign.READ` (or `ZohoCampaigns.campaign.ALL`). Put the refresh token and client credentials in repository secrets:

| Secret | Required | Used as |
| --- | --- | --- |
| `ZOHO_CLIENT_ID` | yes | token refresh |
| `ZOHO_CLIENT_SECRET` | yes | token refresh |
| `ZOHO_REFRESH_TOKEN` | yes | `https://accounts.zoho.eu/oauth/v2/token` |
| `ZOHO_LIST_KEY` | yes | `list_details` key, value `[]` |
| `ZOHO_FROM_EMAIL` | yes | verified sender, today `weeklyupdate@starthawk.io` |
| `ZOHO_TOPIC_ID` | no | sent only when set (required by Zoho if the account uses topic management) |

`from_name` is `Tom at FounderCloud`. The from address has to be a verified Zoho sender or `createCampaign` will reject the draft.

If any required secret is missing, the Zoho job prints a skip message and exits 0. The HTML is still published.

Token refresh and `createCampaign` send parameters in a form-encoded **body** (`resfmt=JSON`). `createCampaign` does not send the email. A later run for the same date sees `campaign_key` and does not create a second draft. Zoho does not re-import HTML into that draft: edit the draft in Zoho, or delete `campaign_key` for that date and re-run to make a new unsent draft.

## Edit a week before it sends

Add `content/YYYY-MM-DD.yml` (this file is optional):

```yaml
subject: "Build Sheet: A live energy prototype needs a CEO."
preheader: "Plus: Why 50/50 equity splits fail, and running an AI startup with zero employees."
hook:
  - First paragraph of Tom's lede.
  - Second paragraph, if you want one.
quote:
  text: "Winners embrace hard work."
  attribution: Lou Holtz
# Optional. Omit to use the rounded live count.
# community_count: "32,500+"
```

`hook` can also be one multi-paragraph string. `quote` can be a single string.

To keep a post or founder out of future issues, add them to `state/featured.json` (`posts[].slug` or `founders[].id`) before the run. Slugs already sent in the May 4, Jul 8, and Sep 12 issues are seeded in code. Founder ids from those issues were not in the redacted mail, so they are not seeded.

Re-running the same date replaces that date's picks and keeps an existing `campaign_key`.

## Local render

```bash
pip install -r requirements.txt
python scripts/build_issue.py --date 2026-10-28
python scripts/zoho_draft.py --date 2026-10-28 \
  --content-url https://tgmarlow.github.io/foundercloud-newsletter/2026-10-28.html
```

Without the Zoho environment variables, `zoho_draft.py` exits 0 and does not call Zoho.

```bash
python -m unittest discover -s tests
```

## Layout

`templates/build_sheet.html.j2` follows the Sep 12 issue: 600px table, logo, blue hero, hook, From the Blog (two posts), The Matchboard (3–4 cards), quote, community line, Tom's sign-off, signature, LinkedIn and Instagram, then the Zoho footer.

Public links point at foundercloud.com (home, `/blog/post/<slug>`, `/profile/<id>`, `/find-a-co-founder`). Logo, signature, and social icons are copied into `issues/assets/` from the Sep 12 campaign CDN so later issues do not depend on that campaign's asset id. Image `src`s are absolute Pages URLs so Zoho's content import can fetch them.

Footer merge tags, which Zoho replaces per recipient:

- `$[FNAME|Founder]$`
- `$[EMAIL]$`
- `$[LI:UPDATEPROFILE]$` (the old mail's preferences link, `ua/upc`)
- `$[LI:UNSUBSCRIBE]$` (unsubscribe, `ua/optout`)

There is no open-tracking pixel and no `maillist-manage` click wrapper. Zoho adds those when you send.

If your account's "update your preferences" page is the newer topic center rather than update-profile, swap the preferences href for `$[LI:SUB_PREF]$`.

## Sample issue

Rendered for **2026-10-28** (the day this pipeline was added). After Pages is set to GitHub Actions and the workflow has run once:

- https://tgmarlow.github.io/foundercloud-newsletter/2026-10-28.html
- https://tgmarlow.github.io/foundercloud-newsletter/latest.html
- https://tgmarlow.github.io/foundercloud-newsletter/

The subject, hook, and quote are `[EDIT ME]` until you add `content/2026-10-28.yml` and re-run, or edit the Zoho draft. Do not send it with those placeholders still in place.

The newest blog post on the public API when this was built was 3 Sep 2026, and that post plus the equity essay were already in the Sep 12 issue, so this sample reaches back to the next two unused essays.
