# Daily Cody

Daily Cody is a GitHub-hosted morning briefing. It sends an email from `Cody Chief of Staff <christian.galler+cody@gmail.com>` to `xx@gmail.com` around 06:00 `Europe/Berlin`.

Inspired by the Daily Dover pattern from Business Insider, Cody combines:

- neutral DWD Open Data MOSMIX weather measurements for `21077 Hamburg-Harburg`, split into morning, midday, and afternoon with temperature, rain probability, and wind in layman terms
- Google Calendar events from `privat`, `Geburtstage`, `A&C`
- Apple Reminders from a local Mac export
- order and delivery emails across merchants, including tracking links when they appear in the email
- yesterday's personal Gmail messages with an actual unanswered request and a source link
- sent Gmail messages from the last 7 days that look like unanswered questions or requests
- a short, factual German briefing without invented commentary
- a morning quote from `data/morning_quotes.json`
- source-backed findings from the separate 05:00 weekday follow-up monitor

The email has both a plain-text and an HTML part. Its HTML weather card renders the three DWD dayparts from structured measurements, with temperature range, rain probability, wind, source, and any weather warning. An additional teal-blue panel above that existing card gives a short German forecast paragraph from the same DWD source, including supported sky conditions, precipitation timing, wind, a nearby forecast temperature and the daytime maximum. The plain-text part includes the identical paragraph, source timestamp/timezone and the original neutral measurements. See [weather overview semantics and preview instructions](docs/weather-overview.md). Deliveries and other sections remain selectable text with working links, not an image; no weather condition is inferred from unavailable data.

Source mode is the default (`CODY_GENERATION_MODE=source`). Weather, deliveries,
all due reminders, follow-up and waiting items are rendered deterministically
from source data. Personal-mail sections exclude newsletters, advertising and
messages from Eveline; completed MeinAuto topics are suppressed. Merchant
confirmations remain eligible for deliveries, including confirmations in Trash.
See [the tested briefing rules](docs/briefing-rules.md).

After explicit consent, the follow-up monitor can hand off private results through
the Actions secret `FOLLOW_UP_SNAPSHOT_JSON`. Cody validates coverage, timestamps
and human mail sources. Until that bridge is configured, recent substantive Gmail
replies populate `Follow-up`, explicitly labelled as live Gmail rather than a
successful full monitor check. Personal findings are never committed to this
public repository. See [the monitor contract](docs/follow-up-monitor.md).

## How It Runs

The briefing lives in GitHub Actions, not on a Mac. A precise external scheduler such as cron-job.org should trigger it at 06:00 `Europe/Berlin` via GitHub's workflow dispatch API. The GitHub schedule remains as a backup and runs every 5 minutes during the UTC morning range that covers Germany's CET and CEST offsets. The script sends once between 06:00 and 08:59 local time in `Europe/Berlin`, so delayed backup schedules can still catch up without drifting into late morning. It skips duplicates if today's briefing was already sent.

You can also run it manually from the GitHub Actions tab. Use `force_send=true`, `allow_duplicate=true`, and `dry_run=true` for a live-data validation that builds the briefing without sending or printing its contents. Leave `allow_duplicate=false` and `dry_run=false` for a real manual send.

Gmail reads are cached within each process and paced against a conservative rolling quota budget. Transient Gmail rate-limit responses are retried with exponential backoff. If the complete process still fails, the workflow makes one fresh attempt after 60 seconds. A persistent data error still leaves the workflow failed and stops it before any incomplete email is sent.

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
    "allow_duplicate": "false",
    "dry_run": "false"
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

Optional, only for explicit AI mode:

- `OPENAI_API_KEY`

`OPENAI_API_KEY` alone no longer enables AI wording. Only the explicit
`CODY_GENERATION_MODE=ai` mode uses it; factual sections remain source-bound.

By default Cody uses `gpt-5.5`. You can override it with the repository variable `OPENAI_MODEL`.

In explicit AI mode, Cody waits up to `OPENAI_TIMEOUT_SECONDS` per attempt,
retries up to `OPENAI_MAX_ATTEMPTS`, and fails without sending if AI still does
not answer. Normal source mode does not make an OpenAI request.

Set the repository variable `ALLOW_TEMPLATE_FALLBACK=true` only if you explicitly prefer a structured current-data email over no email when OpenAI is unavailable.

## Google Access

Create an OAuth client in Google Cloud. A Desktop app client is the easiest option. Approve these scopes:

- Gmail read-only
- Gmail send
- Calendar read-only

Important: make sure the OAuth consent screen is published. If the app stays in
Google's `Testing` publishing status, refresh tokens usually expire after 7
days. Daily Cody will then fail with a Google token refresh error until
`GOOGLE_REFRESH_TOKEN` is replaced again in the GitHub Actions secrets.

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

The Homebrew tap calls the formula `rem-cli` and installs a binary named `rem`, but if Homebrew complains about the formula, use the install command above. Run `rem` once and allow macOS access to Reminders when prompted. The export script can be run manually from a checkout outside iCloud Drive:

```bash
scripts/export_apple_reminders.sh
```

The script updates `data/reminders.json`, writes `data/reminders_export_status.json`, commits the changed export files, and pushes them to GitHub. Daily Cody includes reminders that are overdue, due today, or due in the next two days (seven days on Fridays). Open recurring reminders retain the due date exported by Apple, including overdue dates. Undated open reminders are ignored by default so old inbox/backlog leftovers do not become morning to-dos.

To let the Mac update the export automatically, install the local LaunchAgent:

```bash
scripts/install_reminders_export_agent.sh
```

The agent checks every 30 minutes while the Mac is awake. It exports during the normal `23:59` to `06:59` window, and it also runs a catch-up export outside that window whenever the previous export is older than 1 hour. This repairs a missed overnight export as soon as the Mac is available. The catch-up age can be changed with `REMINDERS_CATCHUP_MAX_AGE_HOURS`.

The installer keeps the runner, a Git checkout, and a stable, locally signed `rem` binary in `~/Library/Application Support/DailyCody`. The LaunchAgent runs from that checkout, keeping its working files and Git index outside iCloud Drive. Reinstalling the agent preserves this checkout and the signed `rem` copy. Set `REMINDERS_REFRESH_CLI=true` only when intentionally replacing the binary; macOS may then request Reminders access again.

The agent syncs its local checkout with `origin/main` before committing and retries the push after a rebase if GitHub rejects it. If that checkout is dirty, staged, or Git reports index trouble, the script publishes through an isolated temporary clone. The source checkout in Documents is no longer used by the agent and may have a different local snapshot; GitHub `main` is the source for the scheduled briefing. Outside the normal export window, the LaunchAgent also wakes the export when local commits are still pending push.

The GitHub workflow requires fresh Reminders by default (`REQUIRE_FRESH_REMINDERS=true`, `REMINDERS_MAX_AGE_HOURS=24`) but it no longer drops the whole briefing when that export is stale. Instead, Daily Cody skips Apple Reminders for that run, adds a short warning to the briefing, and still sends the rest of the morning mail. Set `FAIL_ON_STALE_REMINDERS=true` only if you want the old fail-closed behavior back. If `rem` reports Reminders access denied, run `rem export --incomplete --format json` once from a normal Terminal and allow Reminders access in macOS Privacy settings.

When Daily Cody runs locally, it reads the agent's snapshot if its export timestamp is newer than the checkout's snapshot. If the selected export is missing or stale, it attempts `scripts/export_apple_reminders_if_window.sh` before reading it. Control this with `REFRESH_STALE_REMINDERS`, `REMINDERS_REFRESH_COMMAND`, and `REMINDERS_REFRESH_TIMEOUT_SECONDS`.

If Cody says the export is many hours old, check `~/Library/Logs/DailyCody/reminders-export.err.log` and `~/Library/Application Support/DailyCody/repo/data/reminders_export_status.json`. Compare that timestamp with `data/reminders_export_status.json` on GitHub `main`; a fresh local export does not prove a successful push. Check `launchctl print gui/$(id -u)/com.dailycody.reminders-export` for the agent's last exit code. `reminders access denied` requires a macOS Privacy grant for the stable `rem` binary; `resource deadlock avoided` in the Documents checkout means the agent was not reinstalled with the local checkout.

## Delivery Status

Daily Cody can see shipment and order emails, but it cannot know what physically arrived in the mailbox unless a delivery email says so. For quick manual "done" signals, send Cody an email with one clear marker in the subject or body:

```text
Cody Lieferung erledigt: Kaffeetraum #20111
Cody Lieferung erhalten: Amazon #305-1314679-9745914
Cody, Fix Foxi Album ist angekommen
```

Use a specific marker from the briefing such as the merchant plus order number, product title, or tracking number. Daily Cody reads these Cody-addressed completion notes from the last 180 days and hides matching deliveries from future briefings. Delivered-only updates are not shown in the Deliveries section; they only close older open shipment lines.

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

The reusable detection routine lives in `src/delivery_detection.py` and is documented in `docs/delivery_detection.md`. It can also be run outside Cody with:

```bash
python3 scripts/detect_deliveries.py mails.json
```

Update that routine and its tests whenever a new merchant or carrier pattern is added.

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
- weather location: `21077 Hamburg-Harburg`
- DWD MOSMIX station: `C720` (`HAMBURG-NEUWIEDENTH.`)
- calendar names: `privat,Geburtstage,A&C,MixedCup2026`
- send window: `06:00` until before `09:00`

Adjust `.github/workflows/daily-cody.yml` if those names differ from the exact calendar labels in Google Calendar.
