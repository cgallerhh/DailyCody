# Daily Cody

Daily Cody is a GitHub-hosted morning briefing for Christian Galler. It sends an email from `christian.galler@gmail.com` to `christian.galler@gmail.com` at 07:00 `Europe/Berlin`.

Inspired by the Daily Dover pattern from Business Insider, Cody combines:

- weather for `21077 Hamburg`, including temperature, rain probability, and an umbrella note
- Google Calendar events from `privat`, `Geburtstage`, `A&C`, and `MixedCup2026`
- Google Tasks as an optional GitHub-friendly task source, separate from Apple Reminders
- Amazon order and delivery emails, including tracking links when they appear in the email
- yesterday's Gmail messages that look like they still need a reply, with a short suggested response
- a short, practical German briefing in Cody's voice

## How It Runs

The briefing lives in GitHub Actions, not on a Mac. The workflow runs hourly because GitHub cron uses UTC and Germany changes between CET and CEST. The script only sends when the local time in `Europe/Berlin` is 07:00, and it skips duplicates if today's briefing was already sent.

You can also run it manually from the GitHub Actions tab with `force_send=true`.

## Required GitHub Secrets

Create these secrets in `Settings -> Secrets and variables -> Actions`:

- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`
- `GOOGLE_REFRESH_TOKEN`

Optional, but recommended for the Daily-Dover-style voice:

- `OPENAI_API_KEY`

`OPENAI_API_KEY` is optional. Without it, Cody sends a structured template briefing. With it, Cody writes the more human Daily-Dover-style version.

## Google Access

Create an OAuth client in Google Cloud. A Desktop app client is the easiest option. Approve these scopes:

- Gmail read-only
- Gmail send
- Calendar read-only
- Tasks read-only

Then run the one-time helper:

```bash
python3 scripts/get_google_refresh_token.py
```

Copy the three printed Google values into GitHub Secrets.

If you added Google Tasks after the first setup, enable the Google Tasks API in Google Cloud, rerun the helper, and replace the existing `GOOGLE_REFRESH_TOKEN` secret with the new value. Google Tasks does not automatically sync Apple Reminders.

## Manual Local Test

After setting environment variables locally, run:

```bash
DRY_RUN=true FORCE_SEND=true python3 src/daily_cody.py
```

Remove `DRY_RUN=true` to actually send the email.

## Default Configuration

The workflow already sets:

- sender and recipient: `christian.galler@gmail.com`
- timezone: `Europe/Berlin`
- weather location: `21077 Hamburg`
- calendar names: `privat,Geburtstage,A&C,MixedCup2026`
- send window: `07:00`

Adjust `.github/workflows/daily-cody.yml` if those names differ from the exact calendar labels in Google Calendar.
