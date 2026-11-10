# iPay Boost Support Bot

A Telegram bot with a `/start` welcome screen and `/boost <6-digit UID>` support
command. Welcome-screen buttons update the existing bot message in place. The
bot works in private chats and groups. In a group, each boost response replies
to the user who issued the command and includes that user's Telegram name and
submitted UID.

The bot does not apply or confirm a withdrawal boost and cannot access iPay's
payout queue. It provides basic checks and directs users to human support. There
are no AI replies and no AI provider key is needed. A user can send one valid
boost request every five minutes. Boost requests in media captions are accepted
when the caption includes a six-digit UID and the word `boost`.

## Windows setup

1. Copy `.env.example` to `.env` and set `TELEGRAM_BOT_TOKEN` to your bot token.
   Set `SUPPORT_1` and `SUPPORT_2` to the support usernames you want shown.
2. Install dependencies and start the bot from the project directory:

   ```cmd
   python -m venv .venv
   .venv\Scripts\python.exe -m pip install -r requirements.txt
   .venv\Scripts\python.exe bot.py
   ```

3. Add the bot to your Telegram group. Users can run:

   ```text
   /boost 749488
   ```

   If Telegram requires the bot's username in a group, use
   `/boost@YourBotUsername 749488`.

   For a photo caption in a group with privacy mode enabled, address the
   command to the bot, for example `/boost@YourBotUsername 749488`. A plain
   caption such as `749488 boost my withdrawal` is only delivered when the bot
   is an admin or group privacy is disabled. If you change privacy settings,
   remove the bot from the group and add it again.

The bot publishes `/start` and `/boost` in the Telegram command menu. Common
mistyped commands `/bost`, `/boos`, `/boostt`, and `/bot` also run the same
UID-validated support handler. Other commands and ordinary messages do not
receive bot replies. Each boost request must contain exactly one six-digit UID.
Menu navigation edits the welcome message rather than posting a new message
for each screen.

The Supabase schema includes the persistent boost cooldown table and RPC. If
you update from an earlier schema, rerun `supabase/schema.sql` in the SQL
Editor so the cooldown table, request log, and function are created. The
`users` table keeps the latest UID per Telegram user; each individual attempt
is recorded in `boost_requests`, including whether it was received or blocked
by the cooldown and whether it came from a command or media caption.

## Supabase storage

The bot uses SQLite by default. To store bot data in Supabase, first run
[`supabase/schema.sql`](supabase/schema.sql) once in the Supabase SQL Editor.
Install dependencies with `pip install -r requirements.txt`, then set
`SUPABASE_URL` and `SUPABASE_SECRET_KEY` in the bot's private environment (or
the hosting service's secret settings). The secret key must stay on the bot
server; do not put it in source control or use the publishable key for this
private server-side data. When both variables are set, the bot uses Supabase
for users, settings, custom FAQs, unanswered questions, and message history.
When they are empty, it keeps using SQLite. Existing SQLite rows are not
automatically copied to Supabase.
