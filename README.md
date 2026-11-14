# iPay Telegram Support Bot

This repository contains the existing `python-telegram-bot` polling bot, iPay guide/FAQ replies, `/boost` flow, and an additive question-intelligence and reporting system. The bot uses Supabase when `SUPABASE_URL` and `SUPABASE_SECRET_KEY` are set, and SQLite for local development otherwise. OpenAI is optional: when `OPENAI_API_KEY` is set, it powers conversational answers and multilingual question classification. Classification falls back to deterministic local rules if the API is unset or unavailable.

The bot does not perform, accelerate, or verify withdrawals. Boost replies are acknowledgments only. Question analytics redact six-digit UIDs and skip storing messages that match sensitive-credential checks. Incoming-message activity logs contain message metadata only, not message text.

## Setup

1. Copy `.env.example` to `.env`. Configure `TELEGRAM_BOT_TOKEN`, Supabase credentials, and `ADMIN_USER_IDS` (comma-separated Telegram numeric IDs). `OPENAI_API_KEY` is optional.
2. In Supabase SQL Editor, run the complete [`supabase/schema.sql`](supabase/schema.sql). It includes the existing tables plus the additive question-intelligence objects. The same additive objects are available in [`supabase/migrations/20261111_question_intelligence.sql`](supabase/migrations/20261111_question_intelligence.sql).
3. Install dependencies and run the bot:

   ```powershell
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   .\.venv\Scripts\python.exe bot.py
   ```

   Automatic group screenshot replies use local OCR. Install the Tesseract OCR
   engine on the bot host and make its `tesseract` executable available on
   `PATH`; the Python packages are included in `requirements.txt`. Images are
   processed locally and are not sent to the conversational AI service.

   On Docker deployments, set `TESSERACT_CMD=/usr/bin/tesseract`. The supplied
   `Dockerfile` installs the native OCR executable and sets this value.

4. Keep `.env` and all service keys out of Git. Use the server-only Supabase secret/service-role key; never use a publishable key for private server data.

## Current bot architecture

- `bot.py` owns Telegram command/message/callback handlers, guide replies, the boost cooldown, and configured-admin checks.
- `guide.py`, `knowledge_base.md`, and `problem_resolutions.json` provide the existing guide and resolution content.
- `question_intelligence.py` classifies relevant support messages, applies content redaction, stores individual records, groups similar reports, and creates complete date-range reports. It has SQLite parity for local development.
- `supabase/schema.sql` is the full SQL Editor setup. `supabase/migrations/` contains additive migrations.
- `supabase/functions/daily-question-report/` and `supabase/schedule_daily_report.sql` implement hosted report generation and delivery, independent of the Python bot process.

The classifier uses explicit support/question indicators instead of treating every unknown sentence as an unanswered question. OpenAI classification is optional and runs after the bot reply; local rules remain the fallback. Group collection is limited by Telegram privacy mode and bot membership/permissions. In BotFather, open `/setprivacy`, select this bot, and choose `Disable`; then add the bot to each target group with permission to send messages. This lets Telegram deliver members' image and message updates to the bot. Screenshot responses use `reply_to_message_id`, so Telegram anchors them to the uploaded image.

## Admin Telegram commands

Set `ADMIN_USER_IDS` before using these. They are private admin operations; the bot checks the Telegram numeric user ID.

```text
/questions [status]
/questions status=unanswered category=withdrawal priority=high chat=-100123456 user=123456
/qsearch <words>
/qstatus <question-id> <unanswered|partially_answered|answered|needs_human_review|resolved|ignored>
/qnote <question-id> <investigation note>
/qresolve <question-id> <resolution summary>
/qshow <question-id>
/qhistory <Telegram-user-id>
/qassign <question-id> <admin-Telegram-user-id>
/dailyreport [YYYY-MM-DD] [Timezone]
```

`/dailyreport` defaults to the current date in `REPORT_TIMEZONE` (default `Asia/Kolkata`). It stores an idempotent report in Supabase and sends the complete report as JSON and CSV to the admin who requested it. Reports paginate question rows in batches and retain every question, including duplicates. The legacy `/unanswered` command remains available and reads the older table; use `/questions unanswered` for new records.

## End-of-day delivery while the bot is offline

The Python bot's `/dailyreport` command is manual. For automatic daily generation when Render or the bot is offline, deploy the Edge Function and schedule it in Supabase Cron:

1. Enable the `pg_cron`, `pg_net`, and Vault extensions in the Supabase Dashboard.
2. Link the Supabase CLI to this project, then deploy:

   ```powershell
   supabase functions deploy daily-question-report --no-verify-jwt
   ```

   The function disables gateway JWT verification because Cron calls it service-to-service; it then checks a separate `x-report-secret` value. Keep the function secret private.
3. Set Edge Function secrets in the Supabase Dashboard or CLI:

   ```text
   SUPABASE_SERVICE_ROLE_KEY=<server-only Supabase secret key>
   TELEGRAM_BOT_TOKEN=<bot token>
   REPORT_CHAT_ID=<private admin chat or designated admin group ID>
   REPORT_TIMEZONE=Asia/Kolkata
   DAILY_REPORT_WEBHOOK_SECRET=<long random secret>
   ```

   `SUPABASE_URL` is supplied by hosted Supabase Edge Functions; set it explicitly for local testing if needed.
4. Add these three secrets to Supabase Vault: `ipay_report_url` (project URL), `ipay_report_api_key` (Supabase server secret key), and `ipay_report_secret` (the same value as `DAILY_REPORT_WEBHOOK_SECRET`). Then run [`supabase/schedule_daily_report.sql`](supabase/schedule_daily_report.sql) in SQL Editor. The supplied schedule runs at 23:59 Asia/Kolkata (18:29 UTC) and retries failed/pending delivery every ten minutes.
5. Check Supabase Cron job history and the `daily_reports.delivery_status` field after the first scheduled run.

The report includes message/question totals, categories and statuses, all individual questions and bot responses, repeated problem clusters, group activity, and suggested knowledge-gap reviews. Large exports split into numbered JSON/CSV files rather than dropping rows. Duplicate delivery is prevented after a successful send; failed attempts remain eligible for retry.

## Tests

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Tests use SQLite and mocked APIs. A live Supabase migration, Telegram delivery, and hosted Cron run require the project's external credentials and must be verified after deployment.
