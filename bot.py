import logging
import os
import re
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse

from dotenv import load_dotenv
from openai import APIError, AsyncOpenAI
from telegram import (
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    InputMediaPhoto,
    Update,
)
from telegram.constants import ChatType
from telegram.error import BadRequest, TelegramError
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.request import HTTPXRequest

from guide import Guide, GuidePage

load_dotenv()
logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO").upper(),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
log = logging.getLogger("ipay-bot")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
AI_API_KEY = os.getenv("AI_API_KEY", "").strip()
AI_BASE_URL = os.getenv("AI_BASE_URL", "").strip() or None
AI_MODEL = os.getenv("AI_MODEL", "gpt-4o-mini").strip()
SUPPORT_1 = os.getenv("SUPPORT_1", "@Ipay800").strip()
SUPPORT_2 = os.getenv("SUPPORT_2", "@Roas668").strip()
SUPPORT_3 = os.getenv("SUPPORT_3", "@Ipay811").strip()
START_URL = os.getenv("START_URL", "").strip()
GROUP_SUPPORT_URL = "https://t.me/ipayvip_1"
WELCOME_IMAGE_PATH = Path(
    os.getenv("WELCOME_IMAGE_PATH", "custom_images/welcome_home.jpg")
)
GUIDE_PDF_PATH = Path(os.getenv("GUIDE_PDF_PATH", "I_PAY_User_Guide.pdf"))
GUIDE_IMAGE_DIR = Path(os.getenv("GUIDE_IMAGE_DIR", "guide_pages"))
CUSTOM_IMAGES_DIR = Path(os.getenv("CUSTOM_IMAGES_DIR", "custom_images"))
KNOWLEDGE_PATH = Path(os.getenv("KNOWLEDGE_PATH", "knowledge_base.md"))
DB_PATH = os.getenv("DB_PATH", "ipay_bot.sqlite3")
RATE_LIMIT_SECONDS = max(0.5, float(os.getenv("RATE_LIMIT_SECONDS", "2")))

WELCOME = (
    "✦ WELCOME TO iPAY ✦\n\n"
    "Your guide to wallets, payments, and support.\n\n"
    "◆ Up to 4% commission on eligible completed orders\n"
    "◆ Rewards on selected activity orders\n"
    "◆ Quick access to support\n"
    "◆ A scalable platform for earning opportunities"
)

SYSTEM_RULES = """You are iPay's English-language customer-support assistant.
Use ONLY the provided excerpts from the official iPay guide for iPay policies.
Answer in simple English and give short numbered steps when explaining a process.
Do not invent wallet eligibility, fees, payment status, processing times, reward
eligibility, referral links, or account-specific information. If the excerpts do not
answer the question, say what is not confirmed and offer human support.
The guide describes a 300.00 reward performance quota, not an unlocking fee. Never
ask a user to send a password, OTP, PIN, card detail, or authentication secret.
Never claim to have checked a transaction or contacted support. Mention conflicts in
the guide (including older commission figures) and direct users to support to confirm.
Ask one concise clarifying question if the issue cannot be understood from the message.
"""

SENSITIVE_INPUT = re.compile(
    r"(?i)\b(?:password|passcode|otp|one[- ]time password|verification code|pin)"
    r"\s*(?:is|:|=)?\s*[A-Za-z0-9!@#$%^&*_.-]{3,}"
)
TOKEN_INPUT = re.compile(r"\b\d{6,12}:[A-Za-z0-9_-]{20,}\b")


def _support_link(handle: str, label: str) -> InlineKeyboardButton:
    username = handle.strip().lstrip("@")
    return InlineKeyboardButton(label, url=f"https://t.me/{username}")


def _nav_button() -> InlineKeyboardButton:
    return InlineKeyboardButton("⬅️ Main menu", callback_data="nav:menu")


def main_keyboard() -> InlineKeyboardMarkup:
    start_button = (
        InlineKeyboardButton("▶️ START NOW ›", url=START_URL)
        if START_URL and _url_is_safe(START_URL)
        else InlineKeyboardButton("▶️ START NOW ›", callback_data="menu:start")
    )
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("💳 WALLETS", callback_data="menu:wallets"),
                InlineKeyboardButton("⚠️ ISSUES", callback_data="menu:issues"),
            ],
            [
                InlineKeyboardButton("🛡️ SECURITY", callback_data="menu:security"),
                InlineKeyboardButton("💬 SUPPORT", callback_data="menu:support"),
            ],
            [start_button],
        ]
    )


def wallets_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("💰 PAYIN", callback_data="wallet:payin"),
                InlineKeyboardButton("💸 PAYOUT", callback_data="wallet:payout"),
            ],
            [_nav_button()],
        ]
    )


def issues_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("Deposit problem", callback_data="issue:deposit"),
                InlineKeyboardButton("Withdrawal problem", callback_data="issue:withdrawal"),
            ],
            [
                InlineKeyboardButton("Missing reward", callback_data="issue:rewards"),
                InlineKeyboardButton("Commission", callback_data="issue:commission"),
            ],
            [
                InlineKeyboardButton("Account access", callback_data="issue:account"),
                InlineKeyboardButton("Other payment issue", callback_data="issue:other"),
            ],
            [
                InlineKeyboardButton("UPI blocked / locked", callback_data="issue:upi"),
                InlineKeyboardButton("Order locked", callback_data="issue:locked"),
            ],
            [_support_button(), _nav_button()],
        ]
    )


def support_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                _support_link(SUPPORT_1, f"Contact {SUPPORT_1}"),
                _support_link(SUPPORT_2, f"Contact {SUPPORT_2}"),
            ],
            [_support_link(SUPPORT_3, f"Contact {SUPPORT_3}")],
            [InlineKeyboardButton("👥 Group Support", url=GROUP_SUPPORT_URL)],
            [_nav_button()],
        ]
    )


def support_options_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "👤 Customer Support",
                    callback_data="menu:customer_support",
                )
            ],
            [InlineKeyboardButton("👥 Group Support", url=GROUP_SUPPORT_URL)],
            [_nav_button()],
        ]
    )


def referral_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [_support_button()],
            [_nav_button()],
        ]
    )


def _support_button() -> InlineKeyboardButton:
    return InlineKeyboardButton("💬 Contact support", callback_data="menu:support")


def _url_is_safe(url: str) -> bool:
    parsed = urlparse(url)
    return parsed.scheme == "https" and bool(parsed.netloc)


def start_keyboard() -> InlineKeyboardMarkup:
    if START_URL and _url_is_safe(START_URL):
        return InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("🚀 Open iPay", url=START_URL)],
                [_nav_button()],
            ]
        )
    return InlineKeyboardMarkup(
        [
            [_support_button()],
            [_nav_button()],
        ]
    )


CALLBACK_ROUTES = {
    "nav:menu",
    "menu:wallets",
    "menu:issues",
    "menu:security",
    "menu:support",
    "menu:customer_support",
    "menu:referral",
    "menu:start",
    "wallet:payin",
    "wallet:payout",
    "issue:deposit",
    "issue:withdrawal",
    "issue:rewards",
    "issue:commission",
    "issue:account",
    "issue:other",
    "issue:upi",
    "issue:locked",
}


def _wallet_caption(mode: str = "overview") -> str:
    wallets = (
        "- PhonePe\n"
        "- Paytm\n"
        "- MobiKwik\n"
        "- Freecharge\n"
        "- Google Pay (GPay)\n"
        "- PhonePe Business\n"
        "- Paytm Business"
    )
    note = (
        "The official guide confirms PhonePe, Paytm, MobiKwik, and Freecharge. "
        "It does not confirm Google Pay or Business Wallet eligibility."
    )
    if mode == "payin":
        return (
            f"💰 SUPPORTED WALLETS\n\n"
            f"PAYIN\n\n{wallets}\n\n{note}"
        )
    if mode == "payout":
        return (
            f"💰 SUPPORTED WALLETS\n\n"
            f"PAYOUT\n\n{wallets}\n\n{note}"
        )
    return "💰 SUPPORTED WALLETS"


def db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS messages (
            chat_id INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at INTEGER NOT NULL
        )"""
    )
    return conn


def add_message(chat_id: int, role: str, content: str) -> None:
    with closing(db()) as conn:
        conn.execute(
            "INSERT INTO messages(chat_id, role, content, created_at) VALUES(?,?,?,?)",
            (chat_id, role, content[:4000], int(time.time())),
        )
        conn.execute(
            """DELETE FROM messages WHERE chat_id=? AND rowid NOT IN
               (SELECT rowid FROM messages WHERE chat_id=?
                ORDER BY created_at DESC, rowid DESC LIMIT 12)""",
            (chat_id, chat_id),
        )
        conn.commit()


def get_history(chat_id: int) -> List[Dict[str, str]]:
    with closing(db()) as conn:
        rows = conn.execute(
            "SELECT role, content FROM messages WHERE chat_id=? "
            "ORDER BY created_at ASC, rowid ASC",
            (chat_id,),
        ).fetchall()
    return [{"role": role, "content": content} for role, content in rows]


def _contains_sensitive_input(text: str) -> bool:
    return bool(SENSITIVE_INPUT.search(text) or TOKEN_INPUT.search(text))


async def _send_home_message(message) -> None:
    if not message:
        return
    image_path = WELCOME_IMAGE_PATH
    if image_path.is_file():
        try:
            with image_path.open("rb") as image:
                await message.reply_photo(
                    photo=InputFile(image),
                    caption=WELCOME,
                    reply_markup=main_keyboard(),
                )
            return
        except (OSError, TelegramError) as exc:
            log.error("Could not send the home image (%s)", type(exc).__name__)
    await message.reply_text(WELCOME, reply_markup=main_keyboard())


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _send_home_message(update.effective_message)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(
            "Choose a button or ask a question in English. I can explain wallets, "
            "deposit and withdrawal steps, rewards, referrals, and account-safety "
            "guidance from the supplied guide. I cannot check live accounts or "
            "payments. Never send passwords, OTPs, or PINs.",
            reply_markup=main_keyboard(),
        )


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _send_home_message(update.effective_message)


async def wallets_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _send_wallets(update.effective_message)


async def issues_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(
            "Choose the issue that best matches what happened. For a specific "
            "problem, you can also describe it in a message.",
            reply_markup=issues_keyboard(),
        )


async def security_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _send_security(update.effective_message)


async def support_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _send_support(update.effective_message)


async def referral_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _send_referral(update.effective_message)


async def commission_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message:
        await update.effective_message.reply_text(
            "The supplied guide states 4% for eligible completed orders and an extra "
            "₹5 for selected activity orders. It also contains an older 3% example. "
            "Confirm the live rate and offer conditions in the app or with support.",
            reply_markup=main_keyboard(),
        )


async def rewards_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _send_referral(update.effective_message)


async def payment_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message:
        text, search_query = _issue_answer("deposit")
        guide: Guide = context.application.bot_data["guide"]
        pages = guide.retrieve(search_query, top_k=1)
        if pages:
            await _send_guide_page(update.effective_message, pages[0])
        await update.effective_message.reply_text(
            text,
            reply_markup=support_options_keyboard(),
        )


async def withdrawal_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message:
        text, search_query = _issue_answer("withdrawal")
        guide: Guide = context.application.bot_data["guide"]
        pages = guide.retrieve(search_query, top_k=1)
        if pages:
            await _send_guide_page(update.effective_message, pages[0])
        await update.effective_message.reply_text(
            text,
            reply_markup=support_options_keyboard(),
        )


async def quota_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await _send_custom_image(
        update.effective_message,
        "reward_quota.jpg",
        "Owner-provided quota illustration. The official guide confirms the "
        "300.00 threshold but not its unit or calculation.",
        "The guide says reward funds can be withdrawn after completing payment "
        "tasks totaling 300.00. The unit, qualifying tasks, and calculation are "
        "not specified. This is not a separate unlocking fee. Contact support "
        "for clarification.",
        support_options_keyboard(),
    )


async def _send_wallets(message) -> None:
    if not message:
        return
    await message.reply_text(
        "The supplied guide lists PhonePe, Paytm, MobiKwik, and Freecharge for "
        "placing and receiving/selling orders. Use the wallet linked to iPay and "
        "check order instructions before paying. Business Wallet eligibility is "
        "unclear in the guide, so please confirm it with support.",
        reply_markup=wallets_keyboard(),
    )


async def _send_security(message) -> None:
    if not message:
        return
    await message.reply_text(
        "Keep your password and verification codes private. Never send your "
        "password, OTP, PIN, or card details in chat, including to support. The "
        "guide's signed-in password-change path is Assets > Password; it does not "
        "confirm the Forgot Password or account-recovery process. For suspicious "
        "activity or account access issues, contact human support.",
        reply_markup=support_options_keyboard(),
    )


async def _send_support(message) -> None:
    if not message:
        return
    await message.reply_text(
        "Choose a customer support contact or open the iPay support group below.",
        reply_markup=support_options_keyboard(),
    )


async def _send_referral(message) -> None:
    if not message:
        return
    await message.reply_text(
        "The supplied guide describes ₹100 for the inviter and ₹50 for the invited "
        "friend after registration through a referral link/code and 10 qualifying "
        "orders on the same day. Current availability, qualifying-order rules, and "
        "the exact expiry calculation are not confirmed. Ask support to confirm "
        "the live offer. This bot cannot generate referral links or check referral status.",
        reply_markup=referral_keyboard(),
    )


def _issue_answer(issue: str) -> Tuple[str, str]:
    answers = {
        "deposit": (
            "For a deposit/payment problem:\n"
            "1. Check the recipient, exact amount, wallet, and order expiry.\n"
            "2. Use the wallet linked to iPay; the guide says not to use Pocket UPI.\n"
            "3. If you paid, submit the matching UTR and payment screenshot in iPay.\n"
            "4. If status is uncertain or the order expired, request a status check "
            "before paying again.\n\nContact support if it remains unresolved.",
            "deposit payment order UTR screenshot expired",
        ),
        "withdrawal": (
            "For a delayed withdrawal, check your receiving limit, whether UPI is "
            "enabled, whether your account can receive funds, and whether the wallet "
            "is connected. Keep your order records and ask support to check the "
            "pending withdrawal. Do not submit a duplicate withdrawal. The guide "
            "does not promise a processing time.",
            "withdrawal pending receiving limit UPI wallet",
        ),
        "rewards": (
            "Check the offer's eligibility and completion conditions in the app. "
            "The guide lists welcome tasks but says availability and claim timing "
            "need confirmation. Reward funds have a 300.00 payment-task quota; the "
            "unit and calculation are not explained. This is not a separate "
            "unlocking fee. Ask support to check missing rewards.",
            "rewards welcome tasks quota eligibility",
        ),
        "commission": (
            "The supplied guide states 4% for eligible completed orders and an "
            "extra ₹5 for selected activity orders. It also contains an older "
            "3% example. Confirm the live rate and offer conditions in the app or "
            "with support; this bot cannot check your order eligibility.",
            "commission 4% 3% activity reward eligible",
        ),
        "account": (
            "The guide shows a signed-in password change at Assets > Password, "
            "followed by its verification steps. It does not confirm the Forgot "
            "Password or account-recovery process. Never share your password, OTP, "
            "or PIN here. Contact support for account-specific access problems.",
            "account password OTP security access",
        ),
        "other": (
            "Please describe what happened, without sharing passwords, OTPs, PINs, "
            "or payment credentials. I can explain relevant steps from the guide, "
            "but I cannot verify your live payment or account status.",
            "payment issue status support",
        ),
    }
    return answers.get(issue, answers["other"])


async def _send_guide_page(message, page: GuidePage) -> bool:
    if not message or not page.image_path or not page.image_path.is_file():
        return False
    try:
        with page.image_path.open("rb") as image:
            caption = f"Relevant page from the iPay guide (page {page.page_number})."
            await message.reply_photo(photo=InputFile(image), caption=caption)
        return True
    except TelegramError as exc:
        log.error("Could not send an iPay guide page image (%s)", type(exc).__name__)
        return False


async def _send_custom_image(
    message,
    filename: str,
    caption: str,
    explanation: str,
    keyboard: InlineKeyboardMarkup,
) -> None:
    if not message:
        return
    image_path = CUSTOM_IMAGES_DIR / filename
    if image_path.is_file():
        try:
            with image_path.open("rb") as image:
                await message.reply_photo(
                    photo=InputFile(image),
                    caption=caption,
                )
        except (OSError, TelegramError) as exc:
            log.error("Could not send custom guide image (%s)", type(exc).__name__)
            await message.reply_text(
                "I couldn't send the illustration. The confirmed guidance is below."
            )
    else:
        log.warning("Configured custom guide image is missing: %s", filename)
        await message.reply_text(
            "The illustration is unavailable. The confirmed guidance is below."
        )
    await message.reply_text(explanation, reply_markup=keyboard)


async def _show_wallet_menu(update: Update, mode: str = "overview") -> None:
    query = update.callback_query
    message = query.message if query else None
    if not query or not message:
        return

    image_path = CUSTOM_IMAGES_DIR / "wallets_payin.jpg"
    if not image_path.is_file():
        log.warning("Supported-wallet image is missing: %s", image_path.name)
        await _present_callback(
            update,
            "The supported-wallet image is unavailable.\n\n" + _wallet_caption(mode),
            wallets_keyboard(),
        )
        return

    if message.photo:
        try:
            with image_path.open("rb") as image:
                await query.edit_message_media(
                    media=InputMediaPhoto(
                        media=InputFile(image),
                        caption=_wallet_caption(mode),
                    ),
                    reply_markup=wallets_keyboard(),
                )
                return
        except BadRequest as exc:
            if "message is not modified" in str(exc).lower():
                return
            log.warning("Could not update wallet menu image (%s)", type(exc).__name__)
        except (OSError, TelegramError) as exc:
            log.warning("Could not update wallet menu image (%s)", type(exc).__name__)

    try:
        with image_path.open("rb") as image:
            await message.reply_photo(
                photo=InputFile(image),
                caption=_wallet_caption(mode),
                reply_markup=wallets_keyboard(),
            )
    except (OSError, TelegramError) as exc:
        log.error("Could not send wallet menu image (%s)", type(exc).__name__)
        await _present_callback(
            update,
            "The supported-wallet image could not be sent.\n\n" + _wallet_caption(mode),
            wallets_keyboard(),
        )
        return

    try:
        if message.photo:
            await query.edit_message_reply_markup(
                reply_markup=InlineKeyboardMarkup([])
            )
        else:
            await query.edit_message_text(
                text="Supported-wallet guide is shown below.",
                reply_markup=InlineKeyboardMarkup([]),
            )
    except BadRequest as exc:
        log.warning("Could not clear previous menu buttons (%s)", type(exc).__name__)


async def _present_callback(
    update: Update,
    text: str,
    keyboard: InlineKeyboardMarkup,
) -> None:
    query = update.callback_query
    if not query or not query.message:
        return
    if query.message.photo:
        try:
            await query.edit_message_caption(caption=text[:1024], reply_markup=keyboard)
        except BadRequest as exc:
            if "message is not modified" in str(exc).lower():
                return
            log.error("Could not update the welcome image menu (%s)", type(exc).__name__)
            await query.message.reply_text(text, reply_markup=keyboard)
    else:
        try:
            await query.edit_message_text(text, reply_markup=keyboard)
        except BadRequest as exc:
            if "message is not modified" in str(exc).lower():
                return
            log.error("Could not update the menu message (%s)", type(exc).__name__)
            await query.message.reply_text(text, reply_markup=keyboard)


async def _show_home_menu(update: Update) -> None:
    query = update.callback_query
    message = query.message if query else None
    if not query or not message:
        return
    if not WELCOME_IMAGE_PATH.is_file():
        log.warning("Welcome image is missing: %s", WELCOME_IMAGE_PATH.name)
        await _present_callback(update, WELCOME, main_keyboard())
        return

    if message.photo:
        try:
            with WELCOME_IMAGE_PATH.open("rb") as image:
                await query.edit_message_media(
                    media=InputMediaPhoto(
                        media=InputFile(image),
                        caption=WELCOME,
                    ),
                    reply_markup=main_keyboard(),
                )
            return
        except BadRequest as exc:
            if "message is not modified" in str(exc).lower():
                return
            log.warning("Could not update the home image (%s)", type(exc).__name__)
        except (OSError, TelegramError) as exc:
            log.warning("Could not update the home image (%s)", type(exc).__name__)

    try:
        with WELCOME_IMAGE_PATH.open("rb") as image:
            await message.reply_photo(
                photo=InputFile(image),
                caption=WELCOME,
                reply_markup=main_keyboard(),
            )
    except (OSError, TelegramError) as exc:
        log.error("Could not send the home image (%s)", type(exc).__name__)
        await _present_callback(update, WELCOME, main_keyboard())
        return

    try:
        await query.edit_message_text(
            text="Home menu is shown below.",
            reply_markup=InlineKeyboardMarkup([]),
        )
    except BadRequest as exc:
        log.warning("Could not clear previous menu buttons (%s)", type(exc).__name__)


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await query.answer()
    action = query.data or ""
    guide: Guide = context.application.bot_data["guide"]

    if action == "nav:menu":
        await _show_home_menu(update)
        return
    if action == "menu:wallets":
        await _show_wallet_menu(update)
        return
    if action == "menu:issues":
        await _present_callback(
            update,
            "Choose the issue that best matches what happened, or describe it in a message.",
            issues_keyboard(),
        )
        return
    if action == "menu:security":
        await _present_callback(
            update,
            "Keep passwords, OTPs, and PINs private. The guide's signed-in password "
            "change path is Assets > Password; it does not confirm account recovery. "
            "Contact support about suspicious activity.",
            support_options_keyboard(),
        )
        return
    if action == "menu:support":
        await _present_callback(
            update,
            "Choose customer support to contact an iPay support representative, or "
            "join the iPay group for community support. Never share passwords or OTPs.",
            support_options_keyboard(),
        )
        return
    if action == "menu:customer_support":
        await _present_callback(
            update,
            "Choose a customer support contact below.",
            support_keyboard(),
        )
        return
    if action == "menu:referral":
        await _present_callback(
            update,
            "The guide describes ₹100 for the inviter and ₹50 for the invited friend "
            "after registration through a referral link/code and 10 qualifying "
            "orders on the same day. Current availability and exact conditions "
            "need confirmation with support.",
            referral_keyboard(),
        )
        return
    if action == "menu:start":
        if START_URL and _url_is_safe(START_URL):
            text = "Open the configured iPay starting page using the button below."
        elif START_URL:
            text = "The configured START_URL is not a valid HTTPS URL. Ask the bot administrator to correct it."
        else:
            text = "The official iPay starting URL has not been configured. The bot administrator must set START_URL in .env."
        await _present_callback(update, text, start_keyboard())
        return
    if action in {"wallet:payin", "wallet:payout"}:
        await _show_wallet_menu(update, action.partition(":")[2])
        return
    custom_image_actions = {
        "issue:withdrawal": (
            "withdrawal_issues.jpg",
            "Owner-provided withdrawal illustration. Business Wallet and rebind "
            "claims are not confirmed by the official guide.",
            _issue_answer("withdrawal")[0],
        ),
        "issue:rewards": (
            "reward_quota.jpg",
            "Owner-provided quota illustration. The official guide confirms the "
            "300.00 threshold but not its unit or calculation.",
            "The guide says reward funds can be withdrawn after completing payment "
            "tasks totaling 300.00. The unit, qualifying tasks, and calculation are "
            "not specified. This is not a separate unlocking fee. Contact support "
            "for clarification.",
        ),
        "issue:upi": (
            "upi_blocked.jpg",
            "Owner-provided UPI-lock illustration. A ₹1 transfer is appropriate "
            "only if the iPay app instructs you to do it.",
            "Possible lock causes are not a confirmed diagnosis. Follow the "
            "in-app instructions, avoid repeated attempts while locked, and contact "
            "your bank or wallet provider if it is a provider-level block. Only "
            "make a ₹1 transfer to your own UPI if the app specifically instructs "
            "you. Contact support if the issue remains.",
        ),
        "issue:locked": (
            "order_locked.jpg",
            "Owner-provided order-lock illustration. Rebinding and automatic "
            "unlock timing are not confirmed by the official guide.",
            "Check the wallet state shown in iPay and follow the instructions in "
            "the app. Do not log in through third-party apps or make duplicate "
            "payments. The guide does not confirm that rebinding or automatic "
            "unlocking will resolve this; contact support for account-specific help.",
        ),
    }
    if action in custom_image_actions:
        filename, caption, explanation = custom_image_actions[action]
        await _present_callback(
            update,
            "Here is the requested illustration and confirmed guide-based guidance.",
            main_keyboard(),
        )
        await _send_custom_image(
            query.message,
            filename,
            caption,
            explanation,
            wallets_keyboard()
            if action.startswith("wallet:")
            else support_options_keyboard(),
        )
        return
    if action.startswith("issue:"):
        issue = action.partition(":")[2]
        text, search_query = _issue_answer(issue)
        keyboard = support_options_keyboard()
        pages = guide.retrieve(search_query, top_k=1)
        if pages and query.message and pages[0].image_path:
            await _present_callback(
                update,
                "Relevant guide page and troubleshooting steps follow.",
                main_keyboard(),
            )
            sent_image = await _send_guide_page(query.message, pages[0])
            if sent_image:
                await query.message.reply_text(text, reply_markup=keyboard)
                return
        await _present_callback(update, text, keyboard)
        return

    log.warning("Received unrecognized callback action")
    await _present_callback(
        update,
        "That menu option is not available. Please return to the main menu.",
        main_keyboard(),
    )


async def _ai_answer(
    question: str,
    chat_id: int,
    guide: Guide,
    ai_client: Optional[AsyncOpenAI],
) -> Tuple[str, List[GuidePage]]:
    pages = guide.retrieve(question, top_k=2)
    if ai_client is None:
        return (
            "AI answers are not configured yet. You can use the issue menus for "
            "guide-based steps or contact human support.",
            pages,
        )
    excerpts = "\n\n".join(
        f"Guide excerpt {index + 1}"
        f"{f' (page {page.page_number})' if page.page_number else ''}:\n{page.text}"
        for index, page in enumerate(pages)
    )
    if not excerpts:
        excerpts = "No relevant guide excerpt was found for this question."
    history = get_history(chat_id)[-10:]
    response = await ai_client.chat.completions.create(
        model=AI_MODEL,
        messages=[
            {
                "role": "system",
                "content": f"{SYSTEM_RULES}\n\n{excerpts}",
            },
            *history,
            {"role": "user", "content": question[:2000]},
        ],
        temperature=0.2,
        max_tokens=500,
    )
    answer = (response.choices[0].message.content or "").strip()
    if not answer:
        answer = (
            "I couldn't prepare a reliable answer from the guide. "
            f"Please contact {SUPPORT_1}, {SUPPORT_2}, or {SUPPORT_3}."
        )
    return answer, pages


async def ai_reply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    chat = update.effective_chat
    if not message or not message.text or not chat or chat.type != ChatType.PRIVATE:
        return

    now = time.monotonic()
    last_message_at = context.application.bot_data.setdefault("last_message_at", {})
    if now - last_message_at.get(chat.id, 0) < RATE_LIMIT_SECONDS:
        await message.reply_text("Please wait a moment before sending another question.")
        return
    last_message_at[chat.id] = now

    question = message.text.strip()[:2000]
    if not question:
        return
    if _contains_sensitive_input(question):
        await message.reply_text(
            "For your safety, do not send passwords, OTPs, PINs, bot tokens, or "
            "payment credentials here. I have not forwarded or saved that message. "
            "If you shared an active token, revoke it and generate a replacement."
        )
        return

    guide: Guide = context.application.bot_data["guide"]
    ai_client: Optional[AsyncOpenAI] = context.application.bot_data.get("ai_client")
    chat_id = chat.id
    try:
        answer, pages = await _ai_answer(question, chat_id, guide, ai_client)
        add_message(chat_id, "user", question)
        add_message(chat_id, "assistant", answer)
        if pages and pages[0].image_path:
            await _send_guide_page(message, pages[0])
        await message.reply_text(
            answer
            + f"\n\nNeed a human? Contact {SUPPORT_1}, {SUPPORT_2}, or {SUPPORT_3}."
        )
    except (APIError, sqlite3.Error) as exc:
        log.error("AI reply failed (%s)", type(exc).__name__)
        await message.reply_text(
            "Sorry, I couldn't answer from the guide right now. Please try again "
            f"later or contact {SUPPORT_1}, {SUPPORT_2}, or {SUPPORT_3}.",
            reply_markup=support_options_keyboard(),
        )


async def _post_init(application: Application) -> None:
    commands = [
        BotCommand("start", "Welcome and main menu"),
        BotCommand("help", "How to use the bot"),
        BotCommand("wallets", "Supported wallets"),
        BotCommand("issues", "Troubleshooting menu"),
        BotCommand("security", "Account safety guidance"),
        BotCommand("support", "Contact human support"),
        BotCommand("referral", "Referral offer information"),
        BotCommand("menu", "Return to the main menu"),
    ]
    try:
        await application.bot.set_my_commands(commands)
    except TelegramError:
        log.error("Could not publish the Telegram command list")


def create_guide() -> Guide:
    return Guide(GUIDE_PDF_PATH, KNOWLEDGE_PATH, GUIDE_IMAGE_DIR)


def build_application(
    token: str,
    guide: Optional[Guide] = None,
    ai_client: Optional[AsyncOpenAI] = None,
) -> Application:
    application = (
        Application.builder()
        .token(token)
        .request(
            HTTPXRequest(
                connect_timeout=20.0,
                read_timeout=30.0,
                write_timeout=30.0,
                media_write_timeout=120.0,
            )
        )
        .post_init(_post_init)
        .build()
    )
    application.bot_data["guide"] = guide or create_guide()
    application.bot_data["ai_client"] = ai_client
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("wallets", wallets_command))
    application.add_handler(CommandHandler("issues", issues_command))
    application.add_handler(CommandHandler("security", security_command))
    application.add_handler(CommandHandler("support", support_command))
    application.add_handler(CommandHandler("referral", referral_command))
    application.add_handler(CommandHandler("menu", menu_command))
    application.add_handler(CommandHandler("commission", commission_command))
    application.add_handler(CommandHandler("rewards", rewards_command))
    application.add_handler(CommandHandler("payment", payment_command))
    application.add_handler(CommandHandler("withdrawal", withdrawal_command))
    application.add_handler(CommandHandler("quota", quota_command))
    application.add_handler(CallbackQueryHandler(handle_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, ai_reply))
    return application


def main() -> None:
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError(
            "Missing TELEGRAM_BOT_TOKEN. Copy .env.example to .env and configure it."
        )
    ai_client = None
    if AI_API_KEY:
        client_args = {"api_key": AI_API_KEY}
        if AI_BASE_URL:
            client_args["base_url"] = AI_BASE_URL
        ai_client = AsyncOpenAI(**client_args)
    else:
        log.warning("AI_API_KEY is missing; guide-based menus will work without AI replies")
    guide = create_guide()
    db().close()
    application = build_application(TELEGRAM_BOT_TOKEN, guide, ai_client)
    log.info("Starting iPay bot polling")
    application.run_polling(
        allowed_updates=Update.ALL_TYPES,
        bootstrap_retries=-1,
    )


if __name__ == "__main__":
    main()
