# Daily Cody

Daily Cody is a GitHub-hosted morning briefing for Christian Galler. It sends an email from `Cody Chief of Staff <christian.galler+cody@gmail.com>` to `christian.galler@gmail.com` around 06:00 `Europe/Berlin`.

Inspired by the Daily Dover pattern from Business Insider, Cody combines:

- weather for `21077 Hamburg`, including temperature, rain probability, and a short practical interpretation
- Google Calendar events from `privat`, `Geburtstage`, `A&C`, and `MixedCup2026`
- today's FIFA World Cup matches, including kickoff time and the ARD/ZDF Free-TV sender when found
- Apple Reminders from a local Mac export
- order and delivery emails across merchants, including tracking links when they appear in the email
- yesterday's Gmail messages that look like they still need a reply, with a short suggested response
- sent Gmail messages from the last 7 days that look like unanswered questions or requests
- a short, practical German briefing in Cody's voice
- a morning quote from `data/morning_quotes.json`

## How It Runs

The briefing lives in GitHub Actions, not on a Mac. A precise external scheduler such as cron-job.org should trigger it at 06:00 `Europe/Berlin` via GitHub's workflow dispatch API. The GitHub schedule remains as a backup and runs every 5 minutes during the UTC morning range that covers Germany's CET and CEST offsets. The script sends once between 06:00 and 08:59 local time in `Europe/Berlin`, so delayed backup schedules can still catch up without drifting into late morning. It skips duplicates if today's briefing was already sent.

You can also run it manually from the GitHub Actions tab with `force_send=true`. Leave `allow_duplicate=false` unless you intentionally want a second briefing on the same day.

## Precise 06:00 Scheduler

Create a fine-grained GitHub token for `cgallerhh/DailyCody` with **Actions: Read and write**. Then create a cron-job.org job:

- Schedule: daily at `06:00`
- Timezone: `Europe/Berlin`
- URL: `https://api.github.com/repos/cgallerhh/DailyCody/actions/workflows/daily-cody.yml/dispatches`
- Method: `POST`
- Headers:
  - `Authorization: Bearer YOUR_GITHUB_TOKEN`
  - `Accept: application/vnd.github+json`
  - `Content-Type: application/json`
  - `X-GitHub-Api-Version: 2022-11-28`
- Body:

```json
{
  "ref": "main",
  "inputs": {
    "force_send": "true",
    "allow_duplicate": "false"
  }
}
```

`force_send=true` bypasses the local time window for the exact external trigger. `allow_duplicate=false` keeps the daily duplicate guard active if cron-job.org retries.

Apple Reminders are different from Gmail and Google Calendar: GitHub Actions cannot read them directly because Apple only exposes them through the signed-in Mac. Daily Cody therefore reads `data/reminders.json`, which your Mac can update and push before the morning briefing.

The Bewerbungen Obsidian vault works the same way: GitHub Actions cannot read the local iCloud vault directly. The local export reads the curated dashboard at `LLM-Wiki/BEWERBUNGEN/pages/_core/Bewerbungs-Dashboard.md` and writes `data/application_wiki_snapshot.json`. Daily Cody uses that snapshot for application waiting points and for suppressing outdated follow-up reminders. It does not read `raw/INBOX` files.

## Required GitHub Secrets

Create these secrets in `Settings -> Secrets and variables -> Actions`:

- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `GOOGLE_REFRESH_TOKEN`

Optional, but recommended for the Daily-Dover-style voice:

- `OPENAI_API_KEY`

`OPENAI_API_KEY` is optional. Without it, Cody sends a structured template briefing. With it, Cody writes the more human Daily-Dover-style version.

By default Cody uses `gpt-5.5`. You can override it with the repository variable `OPENAI_MODEL`.

When `OPENAI_API_KEY` is set, Cody treats the OpenAI-written briefing as required. It waits up to `OPENAI_TIMEOUT_SECONDS` per attempt, retries up to `OPENAI_MAX_ATTEMPTS`, and fails without sending if OpenAI still does not answer. This lets the next scheduled GitHub run try again instead of consuming the daily duplicate guard with a less polished template email.

Set the repository variable `ALLOW_TEMPLATE_FALLBACK=true` only if you explicitly prefer a structured current-data email over no email when OpenAI is unavailable.

## Google Access

Create an OAuth client in Google Cloud. A Desktop app client is the easiest option. Approve these scopes:

- Gmail read-only
- Gmail send
- Calendar read-only

Then run the one-time helper:

```bash
python3 scripts/get_google_refresh_token.py
```

Copy the three printed Google values into GitHub Secrets.

## Apple Reminders Export

Install the local Reminders command line tool once:

```bash
curl -fsSL https://rem.sidv.dev/install | bash
```

The Homebrew tap calls the formula `rem-cli` and installs a binary named `rem`, but if Homebrew complains about the formula, use the install command above. Run `rem` once and allow macOS access to Reminders when prompted. Then export your open Apple Reminders into the repository:

```bash
scripts/export_apple_reminders.sh
```

The script updates `data/reminders.json`, writes `data/reminders_export_status.json`, commits the changed export files, and pushes them to GitHub. Daily Cody includes reminders that are overdue, due today, or due in the next 7 days. Undated open reminders are ignored by default so old inbox/backlog leftovers do not become morning to-dos.

To let the Mac update the export automatically, install the local LaunchAgent:

```bash
scripts/install_reminders_export_agent.sh
```

The agent checks every 30 minutes while the Mac is awake and only exports between `23:59` and `06:59`. This gives GitHub Actions fresh Reminders and Bewerbungen dashboard snapshots before the `06:00` briefing whenever the Mac was running overnight.

The GitHub workflow requires a fresh Reminders export by default (`REQUIRE_FRESH_REMINDERS=true`, `REMINDERS_MAX_AGE_HOURS=60`). If the local Mac export fails for more than two mornings, Daily Cody fails instead of sending a briefing based on stale Reminders data. If `rem` reports Reminders access denied, run `rem export --incomplete --format json` once from a normal Terminal and allow Reminders access in macOS Privacy settings.

## Delivery Status

Daily Cody can see shipment and order emails, but it cannot know what physically arrived in the mailbox unless a delivery email says so. For quick manual "done" signals, send yourself an email with this subject:

```text
Cody Lieferung erledigt: Kaffeetraum #20111
```

Use a specific marker from the briefing such as the merchant plus order number or tracking number. Daily Cody reads these self-sent completion notes from the last 90 days and hides matching deliveries from future briefings.

For durable repo-side overrides, use `data/delivery_status.json`:

```json
{
  "completed": [
    "Fix Foxi Album 18",
    "43einhalb Retoure"
  ]
}
```

Any delivery, return, or waiting item matching one of these phrases is hidden from future briefings. Keep entries short and specific.

## Manual Local Test

To render a local sample briefing without Google, Gmail, or OpenAI secrets, run:

```bash
python3 src/daily_cody.py --sample
```

After setting environment variables locally, run the full dry run:

```bash
DRY_RUN=true FORCE_SEND=true python3 src/daily_cody.py
```

Remove `DRY_RUN=true` to actually send the email.

## Default Configuration

The workflow already sets:

- sender: `Cody Chief of Staff <christian.galler+cody@gmail.com>`
- recipient: `christian.galler@gmail.com`
- timezone: `Europe/Berlin`
- weather location: `21077 Hamburg`
- calendar names: `privat,Geburtstage,A&C,MixedCup2026`
- send window: `06:00` until before `09:00`

Adjust `.github/workflows/daily-cody.yml` if those names differ from the exact calendar labels in Google Calendar.
