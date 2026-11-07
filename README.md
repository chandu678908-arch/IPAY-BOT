# iPay Boost Support Bot

A Telegram bot with a `/start` welcome screen and `/boost <6-digit UID>` support
command. Welcome-screen buttons update the existing bot message in place. The
bot works in private chats and groups. In a group, each boost response replies
to the user who issued the command and includes that user's Telegram name and
submitted UID.

The bot does not apply or confirm a withdrawal boost and cannot access iPay's
payout queue. It provides basic checks and directs users to human support. There
are no AI replies and no AI provider key is needed.

## Windows setup

1. Copy `.env.example` to `.env` and set `TELEGRAM_BOT_TOKEN` to your bot token.
   Set `SUPPORT_1` and `SUPPORT_2` to the support usernames you want shown.
2. Install dependencies and start the bot from the project directory:

   ```cmd
   python -m venv .venv
   .venv\Scripts\python.exe -m pip install -r requirements.txt
   .venv\Scripts\python.exe bot.py
   ```

3. Add the bot to your Telegram group. Group privacy can remain enabled:
   Telegram delivers bot commands in groups. Users can run:

   ```text
   /boost 749488
   ```

   If Telegram requires the bot's username in a group, use
   `/boost@YourBotUsername 749488`.

The bot publishes `/start` and `/boost` in the Telegram command menu. Common
mistyped commands `/bost`, `/boos`, `/boostt`, and `/bot` also run the same
UID-validated support handler. Other commands and ordinary messages do not
receive bot replies. Each boost request must contain exactly one six-digit UID.
Menu navigation edits the welcome message rather than posting a new message
for each screen.
