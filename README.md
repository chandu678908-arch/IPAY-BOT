# iPay AI Telegram Bot

An asynchronous Telegram support bot for iPay. It provides the requested welcome
menu and working navigation, issue guidance, support links, PDF page retrieval,
optional AI answers grounded in the supplied guide, and short per-chat history.

## What is included

- `/start`, `/help`, `/wallets`, `/issues`, `/security`, `/support`, `/referral`,
  and `/menu`, plus the existing commission, rewards, payment, withdrawal, and
  quota commands.
- Inline menus for wallets, common issues, security, support, and the configured
  iPay start page. Referral guidance remains available through `/referral`.
- Optional owner-provided wallet and troubleshooting illustrations. Captions
  distinguish these graphics from verified official-guide instructions,
  especially for uncertain Business Wallet, rebinding, and unlock-time claims.
- Updated full-resolution uploaded wallet, welcome, withdrawal, UPI-lock,
  order-lock, and reward-quota images.
- The wallet screen shows a minimal “Supported Wallets” heading. Payin and
  Payout buttons update the caption with their wallet lists while keeping the
  image; Main menu returns to the home screen. Captions distinguish the four
  wallets confirmed by the official guide from unconfirmed GPay/Business Wallet
  eligibility.
- Text retrieval across every page of `I_PAY_User_Guide.pdf`; page text and
  rendered page images are linked by page number. Relevant images are sent with
  issue guidance and natural-language answers.
- OpenAI-compatible chat completions using `AI_API_KEY`, with a short recent
  history per chat. If no AI key is configured, guide-based menus still work.
- Basic per-chat message rate limiting and refusal to forward/store messages
  recognized as containing passwords, OTPs, PINs, or bot tokens.
- Human-support links for unresolved and account-specific issues.
- Separate customer-support contacts and a link to the iPay group support chat.

The bot does not connect to an iPay account API. It cannot verify a transaction,
account, balance, referral, reward, or payment status. It never claims to contact
support on a user's behalf.

## Requirements

- Python 3.11 or newer
- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- An AI API key for AI-powered natural-language responses (optional for menus)
- The supplied `I_PAY_User_Guide.pdf` (included in this project)

**Rotate the Telegram token that was exposed in chat before running the bot.**
Revoke it in BotFather and put the replacement only in the local `.env` file.
Never put credentials in `.env.example`, a screenshot, or this project ZIP.

## Windows setup

1. Extract the project ZIP into a folder.
2. Open PowerShell in that folder and create the local environment file:

   ```powershell
   if (-not (Test-Path .env)) { Copy-Item .env.example .env }
   notepad .env
   ```

3. Set `TELEGRAM_BOT_TOKEN` to the newly generated token and `AI_API_KEY` to
   your AI provider key. Keep `AI_BASE_URL` blank for OpenAI. Set `AI_MODEL` to a
   model available to your provider.
4. Review the supplied `START_URL` and replace it if needed. The bot only uses
   HTTPS URLs for the start button.
5. Install and start:

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\Activate.ps1
   python -m pip install -r requirements.txt
   python bot.py
   ```

6. Open your Telegram bot and test the commands and buttons below.

### Automatically restart while developing

To keep the local bot running and automatically restart it after Python code,
guide, `.env`, or `custom_images` changes, start the watcher in PowerShell:

```powershell
.\.venv\Scripts\python.exe run_bot_watch.py
```

Leave that terminal open. Press `Ctrl+C` to stop the watcher and bot. The watcher
does not display or inspect secret values. If the bot cannot start because its
Telegram token is missing, it stays available and retries after a watched file
changes. This is a local development helper, not a production process manager;
it does not rebuild the ZIP automatically.

`GUIDE_PDF_PATH` defaults to the included PDF. On first startup, the bot extracts
text from every PDF page and renders page images into the ignored `guide_pages`
cache. Later startups reuse the images. The selected iPay lobby graphic is the
default home image for `/start`, `/menu`, and the main-menu button. Change
`WELCOME_IMAGE_PATH` only if you want to use another image. The default welcome
image is JPEG-compressed for faster uploads, and Telegram media uploads use a
longer write timeout for slower connections. The bot also retries temporary
Telegram connection failures during startup; it still needs internet access to
Telegram's Bot API.

The `custom_images` directory contains the selected welcome home image and supplied wallet and issue
illustrations. Their claims are not treated as policy: the bot labels them as
owner-provided and clarifies where the official PDF does not confirm Business
Wallet eligibility, rebinding steps, or unlock timing. The welcome image uses
the iPay lobby graphic supplied by the owner.
The uploaded payment-order screenshot was not added to bot assets because it
contains personal financial/transaction details; do not broadcast it to users.
The supplied quota screenshot is used for `/quota` and missing-reward guidance,
with a caption explaining which details the official guide leaves unconfirmed.

## Configuration

| Variable | Purpose |
|---|---|
| `TELEGRAM_BOT_TOKEN` | Required Telegram bot token |
| `AI_API_KEY` | Optional AI provider key; without it the menus still run |
| `AI_BASE_URL` | Optional OpenAI-compatible API endpoint |
| `AI_MODEL` | AI model name; defaults to `gpt-4o-mini` |
| `START_URL` | Optional confirmed official HTTPS iPay start page |
| `SUPPORT_1`, `SUPPORT_2`, `SUPPORT_3` | Telegram customer-support usernames |
| `GUIDE_PDF_PATH` | Official guide PDF path |
| `GUIDE_IMAGE_DIR` | Generated page-image cache directory |
| `CUSTOM_IMAGES_DIR` | Optional owner-provided wallet/issue illustrations |
| `WELCOME_IMAGE_PATH` | Optional welcome banner image path |
| `KNOWLEDGE_PATH` | Text fallback if the PDF is unavailable |
| `DB_PATH` | Local SQLite history database |
| `RATE_LIMIT_SECONDS` | Minimum time between natural-language messages per chat |

The bot includes a text knowledge base fallback, but PDF page images are available
only when the guide PDF is configured. PDF pages must contain extractable text;
scanned-only pages need OCR before they can be searched.

## Commands and manual checks

Test each command: `/start`, `/help`, `/wallets`, `/issues`, `/security`,
`/support`, `/referral`, and `/menu`. The compatibility commands `/commission`,
`/rewards`, `/payment`, `/withdrawal`, and `/quota` are also available.

From `/start`, test each of the six buttons. Then test every issue choice, wallet
question/deposit/withdrawal shortcuts, contact-support links, return-to-menu
buttons, and `/menu` from a submenu. Send natural-language examples such as:

- “My deposit is pending. Should I pay again?”
- “Which wallets are supported?”
- “How do I reset my password?”
- “What is the referral reward?”
- “My wallet is locked.”

Check that relevant questions attach a page image, unsupported questions do not
receive invented policies, and a message containing an OTP/password/token receives
a safety warning instead of being sent to the AI. Test missing `AI_API_KEY` using
the menus; test missing `TELEGRAM_BOT_TOKEN` by removing it only from a throwaway
environment (do not erase your working credentials).

Run automated checks from the project directory:

```powershell
python -m unittest discover -s tests -v
python -m py_compile bot.py guide.py
```

These checks do not replace a live Telegram/API test. Live testing requires a
rotated Telegram token and, for AI answers, an active AI provider key with billing
enabled. This project has not been deployed or run against live bot credentials.

## Accuracy and safety

- The welcome screen uses the requested 4% commission and Rs 5 activity-reward
  wording. It does not claim individual eligibility.
- The guide includes an older 3% commission example. The bot flags the conflict
  and tells users to confirm the live offer.
- The guide's 300.00 reward quota is described only as payment tasks totaling
  300.00; it is not presented as a fee or unlocking payment.
- The guide's referral offer, wallet rules, password instructions, and other
  uncertain details are qualified and escalated to support.
- Do not share passwords, OTPs, PINs, payment credentials, or bot tokens in chat.
- The local SQLite database stores a limited recent chat history. Secure the
  hosting account and back up/remove the database according to your privacy needs.

## Hosting

Telegram polling requires outbound network access, not an inbound webhook port.
A small always-on virtual machine may run this bot under `systemd`, but free-tier
availability, eligibility, capacity, and uptime depend on the provider and region.
Free web-service tiers may sleep when idle and are not a guarantee of 24/7 polling.
No hosting provider or bot uptime is guaranteed here.

Example Linux VM deployment (adjust paths and username):

1. Create a Linux VM in an eligible free-tier account. Install Python 3.11 or newer,
   upload the project, and open only the SSH access you need.
2. In the project folder, create `.env` with the rotated Telegram token and AI key.
   Do not place `.env` in a public repository or ZIP.
3. Install dependencies and verify it starts:

   ```sh
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   chmod 600 .env
   .venv/bin/python bot.py
   ```

4. Stop the foreground test with Ctrl+C, then create
   `/etc/systemd/system/ipay-bot.service`:

   ```ini
   [Unit]
   Description=iPay AI Telegram Bot
   After=network-online.target
   Wants=network-online.target

   [Service]
   Type=simple
   User=ubuntu
   WorkingDirectory=/home/ubuntu/ipay_ai_telegram_bot
   EnvironmentFile=/home/ubuntu/ipay_ai_telegram_bot/.env
   ExecStart=/home/ubuntu/ipay_ai_telegram_bot/.venv/bin/python /home/ubuntu/ipay_ai_telegram_bot/bot.py
   Restart=on-failure
   RestartSec=10

   [Install]
   WantedBy=multi-user.target
   ```

5. Start and inspect the service:

   ```sh
   sudo systemctl daemon-reload
   sudo systemctl enable --now ipay-bot
   sudo systemctl status ipay-bot
   sudo journalctl -u ipay-bot -f
   ```

Keep the VM and Python environment updated. Do not open an inbound bot port for
polling. Back up or remove the local SQLite database according to your privacy
needs, and test the service after reboot before relying on it.
