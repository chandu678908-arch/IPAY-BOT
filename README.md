# iPay Telegram Support Bot

This repository contains the existing `python-telegram-bot` polling bot, iPay guide/FAQ replies, `/boost` flow, and an additive question-intelligence and reporting system. The bot uses Supabase when `SUPABASE_URL` and `SUPABASE_SECRET_KEY` are set, and SQLite for local development otherwise. Gemini is the only hosted AI provider for conversational replies, using relevant guide/FAQ excerpts and the existing short chat history. Question triage uses deterministic local rules.

The bot does not perform, accelerate, or verify withdrawals. Boost replies are acknowledgments only. Question analytics redact six-digit UIDs and skip storing messages that match sensitive-credential checks. Incoming-message activity logs contain message metadata only, not message text.

## Setup

1. Copy `.env.example` to `.env`. Configure `TELEGRAM_BOT_TOKEN`, Supabase credentials, and `ADMIN_USER_IDS` (comma-separated Telegram numeric IDs).
2. To enable Gemini chat replies and screenshot Vision confirmation, set `GEMINI_API_KEY` and `AI_ENABLED=true`. `GEMINI_MODEL` defaults to `gemini-3.8-flash`; `AI_MAX_OUTPUT_TOKENS` defaults to `300` and `AI_TIMEOUT_SECONDS` to `45`. Keep the key in the ignored local `.env` or Render's Environment settings; never add it to source files or Git. The startup log confirms SDK client construction without printing the key; Gemini API access is confirmed on the first request.
3. In Supabase SQL Editor, run the complete [`supabase/schema.sql`](supabase/schema.sql). It includes the existing tables plus the additive question-intelligence objects. The same additive objects are available in [`supabase/migrations/20261111_question_intelligence.sql`](supabase/migrations/20261111_question_intelligence.sql).
4. Install dependencies and run the bot:

   ```powershell
   .\.venv\Scripts\python.exe -m pip install -r requirements.txt
   .\.venv\Scripts\python.exe bot.py
   ```

   Automatic private-chat and group screenshot replies start with local OCR.
   Dashboard/profile/menu screenshots, unreadable images, and uncertain
   classifications are ignored without an automatic reply. Locking replies
   require a supported transaction-page layout: category-specific Transition
   Information fields or date/status filters with transaction-record labels.
   Isolated navigation labels, summary totals, monetary values, and status
   words do not qualify.
   When local OCR finds a high-evidence locking/pending candidate and a Gemini
   client is configured, the resized screenshot is sent to Gemini Vision for
   structured confirmation. A mismatch or invalid response results in no locking
   template; a Gemini API outage falls back to the strict local OCR classification.
   `SCREENSHOT_GEMINI_CONFIDENCE_THRESHOLD` defaults to
   `0.90`; the model's confidence is an estimate, not a calibrated probability.
   Without a configured Gemini client, strict local OCR remains the fallback.
   Screenshot bytes and OCR text are not logged or persisted by this classifier.
   The per-user exact-image cooldown defaults to 300 seconds and can be set with
   `SCREENSHOT_IMAGE_COOLDOWN_SECONDS`. Non-default values require the
   three-argument Supabase RPC overload provided by the cooldown configuration
   or recovery migration.
   The Docker image
   installs Tesseract at `/usr/bin/tesseract` and runs an actual generated-image
   OCR smoke test during the build; local hosts need Tesseract available on
   `PATH` or configured with `TESSERACT_CMD`. Each Tesseract pass is limited to
   30 seconds by default; set `SCREENSHOT_OCR_TIMEOUT_SECONDS` to tune it.

   For an existing Supabase project, review and approve both
   [`supabase/migrations/20261114_screenshot_reply_claims.sql`](supabase/migrations/20261114_screenshot_reply_claims.sql)
   and [`supabase/migrations/20261114_screenshot_image_cooldowns.sql`](supabase/migrations/20261114_screenshot_image_cooldowns.sql)
   before running them. The recommended recovery migration is documented in
   [Supabase cooldown migration and verification](#supabase-cooldown-migration-and-verification).
   The first provides persistent per-message processing
   claims; the second atomically tracks exact-image SHA-256 cooldowns per
   Telegram user for five minutes. Until the cooldown migration is applied,
   the bot logs the Supabase RPC failure and falls back to local SQLite, which
   does not provide cross-instance persistence.

5. Keep `.env` and all service keys out of Git. Use the server-only Supabase secret/service-role key; never use a publishable key for private server data.

## Windows OCR setup

Python packages do not install the Tesseract executable. Upstream Tesseract
documentation says newer Windows installers are third-party builds and links
to the UB Mannheim build. Start at the [upstream download guidance](https://github.com/tesseract-ocr/tessdoc/blob/main/Downloads.md), then use its
[UB Mannheim installer page](https://digi.bib.uni-mannheim.de/tesseract/).
Choose the current 64-bit installer and include English (`eng`) language data.
Do not reinstall Python.

During installation, note the actual folder containing `tesseract.exe`. Add
that folder (not the executable filename) to Windows `Path` through **Edit the
system environment variables → Environment Variables → Path**, then reopen
PowerShell and the IDE. Alternatively set `TESSERACT_CMD` in `.env` to the
full executable path, for example:

```text
TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe
```

The bot resolves an explicit `TESSERACT_CMD`, a PATH command, then common
Windows install roots. It reports the detected version and language data at
startup; OCR is ready only when `eng` is available. Verify the installation:

```powershell
where.exe tesseract
tesseract --version
tesseract --list-langs
python --version
python -m pip show pytesseract
python -m pip show Pillow
python -m unittest discover -s tests -v
```

This project currently targets Python 3.12 in Docker. The inspected local
system and project virtual environment both report Python 3.14.6; use a Python
3.12 environment for parity with the deployment image. Installing Tesseract
does not require changing or reinstalling Python.

## Supabase cooldown migration and verification

For an existing project, first apply the reviewed
[`20261114_screenshot_reply_claims.sql`](supabase/migrations/20261114_screenshot_reply_claims.sql),
then apply the additive
[`20261116_screenshot_cooldown_rpc_recovery.sql`](supabase/migrations/20261116_screenshot_cooldown_rpc_recovery.sql)
in the Supabase SQL Editor. The recovery migration defines both RPC signatures
used by the bot (the two-argument five-minute default and the three-argument
configurable cooldown) and asks PostgREST to reload its schema cache. Do not
apply production SQL until you have reviewed and approved it. The older
cooldown migrations remain available for existing installations.

To inspect registered function signatures in SQL Editor:

```sql
select p.proname, pg_get_function_identity_arguments(p.oid) as arguments,
       pg_get_function_result(p.oid) as result
from pg_proc p
join pg_namespace n on n.oid = p.pronamespace
where n.nspname = 'public'
  and p.proname = 'reserve_screenshot_image_cooldown'
order by arguments;
```

Expect signatures `(p_user_id bigint, p_image_hash text)` and
`(p_user_id bigint, p_image_hash text, p_cooldown_seconds integer)`, each
returning `boolean`. This checks PostgreSQL registration, not the PostgREST API
or service-role grants. After applying SQL, restart the bot with the server-only
Supabase credentials, send the same image twice as the same test user, and
confirm the logs no longer report `PGRST202` and the second image follows the
duplicate path. Do not paste credentials into logs or chat.

## Render Docker deployment

This repository has a `Dockerfile` but no `render.yaml` or `Procfile`. In the
Render Dashboard, create or update a **Docker** web service for the repository
and branch you use. The image starts with `python bot.py`; it installs Linux
Tesseract and runs the OCR integration tests during image build. Set service
environment values from [`render.env.example`](render.env.example) in the
Dashboard, including `TELEGRAM_BOT_TOKEN`, `SUPABASE_URL`,
`SUPABASE_SECRET_KEY`, `GEMINI_API_KEY`, and `ADMIN_USER_IDS` as applicable.
Keep secret values only in Render's environment settings. Render supplies
`PORT`; the bot's HTTP health endpoint responds with status 200 at `/`.

The health endpoint checks process reachability only; it does not prove that
Telegram polling, Supabase, Gemini, or OCR is working. Keep one active polling
instance per Telegram token. After an approved deployment, inspect Render logs
for startup diagnostics, polling conflicts, Supabase schema errors, and the
Tesseract readiness/version/language line. A free Render instance may sleep;
the health endpoint does not keep it awake.

## Rollback

For an application rollback, redeploy the last known-good image or source
revision from the Render Dashboard. Do not use a destructive Git reset. The
screenshot cooldown migrations are additive and do not delete records; leave
their tables/functions in place during an application rollback. Do not drop
database objects as a rollback shortcut. Any database reversal requires a
separately reviewed plan and a verified backup. The scheduled report Edge
Function/Cron setup is optional and has its own instructions below.

## Current bot architecture

- `bot.py` owns Telegram command/message/callback handlers, guide replies, Boost message classification, the `/boost` command cooldown, and configured-admin checks.
- `guide.py`, `knowledge_base.md`, and `problem_resolutions.json` provide the existing guide and resolution content.
- `question_intelligence.py` classifies relevant support messages, applies content redaction, stores individual records, groups similar reports, and creates complete date-range reports. It has SQLite parity for local development.
- `supabase/schema.sql` is the full SQL Editor setup. `supabase/migrations/` contains additive migrations.
- `supabase/functions/daily-question-report/` and `supabase/schedule_daily_report.sql` implement hosted report generation and delivery, independent of the Python bot process.

The classifier uses explicit support/question indicators instead of treating every unknown sentence as an unanswered question. Question analytics use deterministic local rules and run after the bot reply. Telegram privacy mode is enabled by default and can prevent the bot from receiving ordinary group photos. For group screenshots, add the bot as an admin or in BotFather open `/setprivacy`, select this bot, choose `Disable`, then remove and re-add it to each target group. A private-chat image test bypasses this group setting. Group collection also depends on bot membership and permission to send messages. Screenshot replies require transaction-specific OCR evidence; generic dashboard terms such as `Withdraw`, `Wallet`, and `Balance` do not qualify. If Gemini Vision is configured, it must independently confirm the OCR category and confidence threshold before a template is sent. Replies use `reply_to_message_id` and are atomically deduplicated by chat and message ID. Duplicate images are detected by exact SHA-256 bytes scoped to the sender's Telegram user ID across chats; the repeated message alone is deleted and receives an ID-bearing warning. Resized or recompressed images are treated as different images.

Boost text and media captions use normalized, explicit request patterns and require a single valid six-digit UID before returning the existing randomized acknowledgment. Status questions are directed to the iPAY app or Customer Support; user text alone is never treated as proof that a Boost was applied. Boost message IDs are atomically deduplicated in the bot's local SQLite database for 30 days. `/boost <UID>` remains available with its existing five-minute per-user cooldown.

The bot uses Telegram long polling. A Render free instance can sleep when idle,
so polling alone does not guarantee immediate responses; use an always-on
deployment for production availability. The HTTP health endpoint is a status
check, not a guarantee that a sleeping free instance will remain awake.

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

## Issue spike alerts

Spike alerts are disabled until `ISSUE_SPIKE_CHAT_ID` is set to the private
admin destination or admin group. They count explicit status reports for
`ORDER_LOCKED`, `WITHDRAWAL_LOCKED`, `WITHDRAWAL_PENDING`, `DEPOSIT_PENDING`,
and `PAYMENT_FAILED`. Defaults are 10 reports in a 15-minute window, with a
15-minute alert cooldown. Alerts contain only category, report count, window,
and distinct-user count; they do not include message text or usernames.

Apply [`supabase/migrations/20261116_issue_spike_alerts.sql`](supabase/migrations/20261116_issue_spike_alerts.sql)
before enabling this on Supabase. It creates a 30-day metadata history and an
atomic alert-claim function. The SQLite fallback creates equivalent local
tables automatically. Configure `ISSUE_SPIKE_THRESHOLD`,
`ISSUE_SPIKE_WINDOW_MINUTES`, and `ISSUE_SPIKE_ALERT_COOLDOWN_SECONDS` to
change the defaults.

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
