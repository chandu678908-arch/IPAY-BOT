import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pymupdf
from openai import APIError
from telegram.ext import CommandHandler

import bot
from guide import Guide
from run_bot_watch import watched_files


class BotTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.knowledge_path = self.root / "knowledge_base.md"
        self.knowledge_path.write_text(
            "# Wallets\nPhonePe and Paytm are supported wallets.\n\n"
            "# Payments\nCheck the recipient, exact amount, and expiry before paying.\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_http_request_logs_do_not_emit_tokens_at_info_level(self):
        self.assertGreaterEqual(
            bot.logging.getLogger("httpx").getEffectiveLevel(),
            bot.logging.WARNING,
        )
        self.assertGreaterEqual(
            bot.logging.getLogger("httpcore").getEffectiveLevel(),
            bot.logging.WARNING,
        )

    def make_guide(self):
        return Guide(
            self.root / "missing.pdf",
            self.knowledge_path,
            self.root / "images",
        )

    def test_every_callback_button_has_a_handler(self):
        with patch.object(bot, "START_URL", ""):
            keyboards = [
                bot.main_keyboard(),
                bot.wallets_keyboard(),
                bot.issues_keyboard(),
                bot.support_keyboard(),
                bot.support_options_keyboard(),
                bot.referral_keyboard(),
                bot.start_keyboard(),
            ]
        callback_actions = {
            button.callback_data
            for keyboard in keyboards
            for row in keyboard.inline_keyboard
            for button in row
            if button.callback_data
        }
        self.assertTrue(callback_actions)
        self.assertEqual(callback_actions - bot.CALLBACK_ROUTES, set())
        self.assertTrue(
            {"menu:wallets", "menu:issues", "menu:security", "menu:support",
             "menu:customer_support", "menu:start", "nav:menu"} <= callback_actions
        )
        self.assertNotIn(
            "menu:referral",
            {
                button.callback_data
                for row in bot.main_keyboard().inline_keyboard
                for button in row
            },
        )
        with patch.object(bot, "START_URL", ""):
            self.assertEqual(
                bot.main_keyboard().inline_keyboard[-1][0].callback_data,
                "menu:start",
            )
        self.assertEqual(
            len(bot.main_keyboard().inline_keyboard[-1]),
            1,
        )

    def test_start_now_button_uses_configured_https_url(self):
        start_url = (
            "https://share.ipaynow.net/?data="
            "dWlkPTg5NDIzNSZjaGFubmVsPTEwMDEmYnVzaW5lc3NfaWQ9OTAwMDAy"
        )
        with patch.object(bot, "START_URL", start_url):
            button = bot.main_keyboard().inline_keyboard[-1][0]
        self.assertEqual(button.url, start_url)
        self.assertIsNone(button.callback_data)

    def test_support_menu_offers_customer_and_group_support(self):
        buttons = [
            button
            for row in bot.support_options_keyboard().inline_keyboard
            for button in row
        ]
        customer_button = next(
            button for button in buttons if button.callback_data == "menu:customer_support"
        )
        group_button = next(
            button for button in buttons if button.text == "👥 Group Support"
        )
        self.assertEqual(customer_button.text, "👤 Customer Support")
        self.assertEqual(group_button.url, "https://t.me/ipayvip_1")

    def test_customer_support_contacts_also_offer_group_support(self):
        keyboard = bot.support_keyboard()
        customer_buttons = keyboard.inline_keyboard[0]
        third_customer_button = keyboard.inline_keyboard[1][0]
        group_button = keyboard.inline_keyboard[2][0]
        self.assertEqual(
            [button.text for button in customer_buttons],
            ["Contact @Ipay800", "Contact @Roas668"],
        )
        self.assertEqual(third_customer_button.text, "Contact @Ipay811")
        self.assertEqual(third_customer_button.url, "https://t.me/Ipay811")
        self.assertEqual(group_button.text, "👥 Group Support")
        self.assertEqual(group_button.url, "https://t.me/ipayvip_1")

    def test_wallet_menu_has_only_payin_payout_and_main_menu_buttons(self):
        buttons = [
            button
            for row in bot.wallets_keyboard().inline_keyboard
            for button in row
        ]
        self.assertEqual(
            [button.callback_data for button in buttons],
            ["wallet:payin", "wallet:payout", "nav:menu"],
        )

    def test_all_commands_are_registered(self):
        application = bot.build_application(
            "dummy-telegram-token-for-tests",
            guide=self.make_guide(),
        )
        commands = {
            command
            for handler in application.handlers[0]
            if isinstance(handler, CommandHandler)
            for command in handler.commands
        }
        self.assertTrue(
            {
                "start", "help", "wallets", "issues", "security",
                "support", "referral", "menu", "commission", "rewards",
                "payment", "withdrawal", "quota",
            } <= commands
        )

    def test_every_callback_action_runs(self):
        guide = self.make_guide()
        custom_images = self.root / "custom_images"
        custom_images.mkdir()
        for filename in (
            "wallets_payin.jpg",
            "withdrawal_issues.jpg",
            "upi_blocked.jpg",
            "order_locked.jpg",
            "reward_quota.jpg",
        ):
            (custom_images / filename).write_bytes(b"test image")
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={"guide": guide})
        )
        for action in bot.CALLBACK_ROUTES:
            with (
                self.subTest(action=action),
                patch.object(bot, "START_URL", ""),
                patch.object(bot, "CUSTOM_IMAGES_DIR", custom_images),
            ):
                message = SimpleNamespace(
                    photo=[],
                    reply_text=AsyncMock(),
                    reply_photo=AsyncMock(),
                )
                query = SimpleNamespace(
                    data=action,
                    message=message,
                    answer=AsyncMock(),
                    edit_message_text=AsyncMock(),
                    edit_message_caption=AsyncMock(),
                    edit_message_media=AsyncMock(),
                    edit_message_reply_markup=AsyncMock(),
                )
                update = SimpleNamespace(callback_query=query)
                asyncio.run(bot.handle_callback(update, context))
                query.answer.assert_awaited_once()
                self.assertTrue(
                    query.edit_message_text.await_count
                    or query.edit_message_caption.await_count
                )

    def test_every_command_handler_returns_a_response(self):
        guide = self.make_guide()
        message = SimpleNamespace(
            reply_text=AsyncMock(),
            reply_photo=AsyncMock(),
        )
        update = SimpleNamespace(effective_message=message)
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={"guide": guide})
        )
        commands = [
            bot.start,
            bot.help_command,
            bot.wallets_command,
            bot.issues_command,
            bot.security_command,
            bot.support_command,
            bot.referral_command,
            bot.menu_command,
            bot.commission_command,
            bot.rewards_command,
            bot.payment_command,
            bot.withdrawal_command,
            bot.quota_command,
        ]
        with patch.object(bot, "WELCOME_IMAGE_PATH", self.root / "missing.jpg"):
            for command in commands:
                with self.subTest(command=command.__name__):
                    message.reply_text.reset_mock()
                    message.reply_photo.reset_mock()
                    asyncio.run(command(update, context))
                    self.assertGreater(
                        message.reply_text.await_count
                        + message.reply_photo.await_count,
                        0,
                    )

    def test_start_uses_supplied_home_image_and_welcome_text(self):
        image_path = self.root / "welcome_home.png"
        image_path.write_bytes(b"test image")
        message = SimpleNamespace(
            reply_text=AsyncMock(),
            reply_photo=AsyncMock(),
        )
        with patch.object(bot, "WELCOME_IMAGE_PATH", image_path):
            asyncio.run(
                bot.start(
                    SimpleNamespace(effective_message=message),
                    SimpleNamespace(),
                )
            )
        message.reply_photo.assert_awaited_once()
        caption = message.reply_photo.await_args.kwargs["caption"]
        self.assertIn("WELCOME TO iPAY", caption)
        self.assertIn("Up to 4% commission on eligible completed orders", caption)
        self.assertIn("Rewards on selected activity orders", caption)
        self.assertIn("Quick access to support", caption)
        self.assertIn("scalable platform for earning opportunities", caption)
        self.assertNotIn("Choose an option below to get started.", caption)
        self.assertNotIn("Offers and eligibility should be confirmed", caption)
        self.assertLessEqual(len(caption), 1024)
        start_button = bot.main_keyboard().inline_keyboard[-1][0]
        self.assertEqual(start_button.text, "▶️ START NOW ›")

    def test_uploaded_home_image_is_compressed_and_preserves_resolution(self):
        image_path = (
            Path(__file__).resolve().parents[1]
            / "custom_images"
            / "welcome_home.jpg"
        )
        data = image_path.read_bytes()
        self.assertEqual(data[:3], b"\xff\xd8\xff")
        image = pymupdf.Pixmap(image_path)
        self.assertEqual((image.width, image.height), (1367, 768))
        self.assertLess(image_path.stat().st_size, 500_000)

    def test_telegram_media_upload_timeout_is_extended(self):
        application = bot.build_application(
            "dummy-telegram-token-for-tests",
            guide=self.make_guide(),
        )
        request = application.bot.request
        self.assertEqual(request._media_write_timeout, 120.0)
        self.assertEqual(request._client.timeout.connect, 20.0)
        self.assertEqual(request._client.timeout.read, 30.0)
        self.assertEqual(request._client.timeout.write, 30.0)

    def test_main_menu_callback_restores_home_image(self):
        image_path = (
            Path(__file__).resolve().parents[1]
            / "custom_images"
            / "welcome_home.jpg"
        )
        message = SimpleNamespace(
            photo=[object()],
            reply_photo=AsyncMock(),
            reply_text=AsyncMock(),
        )
        query = SimpleNamespace(
            data="nav:menu",
            message=message,
            answer=AsyncMock(),
            edit_message_media=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        update = SimpleNamespace(callback_query=query)
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={"guide": self.make_guide()})
        )
        with patch.object(bot, "WELCOME_IMAGE_PATH", image_path):
            asyncio.run(bot.handle_callback(update, context))
        query.edit_message_media.assert_awaited_once()
        media = query.edit_message_media.await_args.kwargs["media"]
        self.assertEqual(media.media.filename, "welcome_home.jpg")
        self.assertIn("WELCOME TO iPAY", media.caption)

    def test_pdf_text_retrieval_and_page_rendering(self):
        pdf_path = self.root / "guide.pdf"
        with pymupdf.open() as document:
            first = document.new_page()
            first.insert_text((72, 72), "PhonePe and Paytm wallet support")
            second = document.new_page()
            second.insert_text((72, 72), "Check the payment recipient and expiry")
            document.save(pdf_path)

        guide = Guide(pdf_path, self.knowledge_path, self.root / "images")
        self.assertEqual(len(guide.pages), 2)
        self.assertTrue(all(page.image_path.is_file() for page in guide.pages))
        page = guide.retrieve("PhonePe wallet", top_k=1)[0]
        self.assertEqual(page.page_number, 1)
        self.assertIn("PhonePe", page.text)

    def test_uploaded_wallet_and_issue_images_keep_full_resolution(self):
        project_root = Path(__file__).resolve().parents[1]
        for filename in (
            "wallets_payin.jpg",
            "withdrawal_issues.jpg",
            "upi_blocked.jpg",
            "order_locked.jpg",
        ):
            with self.subTest(filename=filename):
                document = pymupdf.open(
                    project_root / "custom_images" / filename
                )
                try:
                    self.assertGreaterEqual(document[0].rect.width, 1500)
                    self.assertGreaterEqual(document[0].rect.height, 900)
                finally:
                    document.close()

    def test_watcher_tracks_code_config_and_images_but_ignores_generated_files(self):
        project = self.root / "project"
        (project / "custom_images").mkdir(parents=True)
        (project / "guide_pages").mkdir()
        (project / ".venv" / "Lib").mkdir(parents=True)
        (project / "bot.py").write_text("print('bot')", encoding="utf-8")
        (project / "guide.py").write_text("print('guide')", encoding="utf-8")
        (project / ".env").write_text("TELEGRAM_BOT_TOKEN=", encoding="utf-8")
        (project / "custom_images" / "wallet.jpg").write_bytes(b"image")
        (project / "guide_pages" / "generated.jpg").write_bytes(b"cache")
        (project / ".venv" / "Lib" / "ignored.py").write_text(
            "ignored",
            encoding="utf-8",
        )
        (project / "README.md").write_text("documentation", encoding="utf-8")

        files = watched_files(project)
        self.assertEqual(
            set(files),
            {"bot.py", "guide.py", ".env", "custom_images\\wallet.jpg"},
        )

    def test_supplied_guide_selects_the_withdrawal_page(self):
        project_root = Path(__file__).resolve().parents[1]
        guide = Guide(
            project_root / "I_PAY_User_Guide.pdf",
            self.knowledge_path,
            self.root / "images",
        )
        page = guide.retrieve(
            "withdrawal pending wallet receiving UPI",
            top_k=1,
        )[0]
        self.assertEqual(page.page_number, 8)

    def test_ai_receives_relevant_guide_excerpt(self):
        guide = self.make_guide()
        completion = SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Check the order details."))]
        )
        create = AsyncMock(return_value=completion)
        client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=create),
            )
        )
        with patch.object(bot, "DB_PATH", self.root / "history.sqlite3"):
            answer, pages = asyncio.run(
                bot._ai_answer("What should I check before paying?", 42, guide, client)
            )
        self.assertEqual(answer, "Check the order details.")
        self.assertTrue(pages)
        messages = create.await_args.kwargs["messages"]
        self.assertIn("recipient", messages[0]["content"])

    def test_missing_ai_key_keeps_guide_menus_available(self):
        answer, pages = asyncio.run(
            bot._ai_answer(
                "Which wallets are supported?",
                42,
                self.make_guide(),
                None,
            )
        )
        self.assertIn("not configured", answer)
        self.assertTrue(pages)

    def test_sensitive_inputs_are_not_forwarded(self):
        self.assertTrue(bot._contains_sensitive_input("my OTP is 123456"))
        self.assertTrue(
            bot._contains_sensitive_input(
                "123456789" + ":" + "A" * 25
            )
        )
        self.assertFalse(bot._contains_sensitive_input("My deposit is delayed"))

    def test_sensitive_message_does_not_reach_ai_or_history(self):
        message = SimpleNamespace(
            text="My OTP is 123456",
            reply_text=AsyncMock(),
        )
        update = SimpleNamespace(
            effective_message=message,
            effective_chat=SimpleNamespace(type=bot.ChatType.PRIVATE, id=43),
        )
        context = SimpleNamespace(
            application=SimpleNamespace(
                bot_data={
                    "guide": self.make_guide(),
                    "ai_client": object(),
                    "last_message_at": {},
                }
            )
        )
        with (
            patch.object(bot, "_ai_answer", new=AsyncMock()) as ai_answer,
            patch.object(bot, "add_message") as add_message,
        ):
            asyncio.run(bot.ai_reply(update, context))
        ai_answer.assert_not_awaited()
        add_message.assert_not_called()
        self.assertIn("do not send", message.reply_text.await_args.args[0])

    def test_ai_failure_returns_a_support_fallback(self):
        message = SimpleNamespace(
            text="My payment is delayed",
            reply_text=AsyncMock(),
        )
        update = SimpleNamespace(
            effective_message=message,
            effective_chat=SimpleNamespace(type=bot.ChatType.PRIVATE, id=44),
        )
        context = SimpleNamespace(
            application=SimpleNamespace(
                bot_data={
                    "guide": self.make_guide(),
                    "ai_client": object(),
                    "last_message_at": {},
                }
            )
        )
        with patch.object(
            bot,
            "_ai_answer",
            new=AsyncMock(
                side_effect=APIError(
                    "provider unavailable",
                    request=None,
                    body=None,
                )
            ),
        ):
            asyncio.run(bot.ai_reply(update, context))
        self.assertIn("couldn't answer", message.reply_text.await_args.args[0])

    def test_relevant_page_image_is_sent_with_a_caption(self):
        image_path = self.root / "page.jpg"
        image_path.write_bytes(b"test image")
        message = SimpleNamespace(reply_photo=AsyncMock())
        page = SimpleNamespace(page_number=3, image_path=image_path)
        sent = asyncio.run(bot._send_guide_page(message, page))
        self.assertTrue(sent)
        message.reply_photo.assert_awaited_once()
        self.assertIn("page 3", message.reply_photo.await_args.kwargs["caption"])

    def test_missing_telegram_token_fails_explicitly(self):
        with patch.object(bot, "TELEGRAM_BOT_TOKEN", ""):
            with self.assertRaisesRegex(RuntimeError, "Missing TELEGRAM_BOT_TOKEN"):
                bot.main()

    def test_missing_start_url_does_not_invent_destination(self):
        with patch.object(bot, "START_URL", ""):
            keyboard = bot.start_keyboard()
        buttons = [
            button
            for row in keyboard.inline_keyboard
            for button in row
        ]
        self.assertFalse(any(button.url for button in buttons))
        self.assertIn(
            "nav:menu",
            {button.callback_data for button in buttons},
        )

    def test_custom_withdrawal_image_disclaims_unverified_graphic_claims(self):
        custom_images = self.root / "custom_images"
        custom_images.mkdir()
        (custom_images / "withdrawal_issues.jpg").write_bytes(b"test image")
        query = SimpleNamespace(
            data="issue:withdrawal",
            message=SimpleNamespace(
                photo=[],
                reply_text=AsyncMock(),
                reply_photo=AsyncMock(),
            ),
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
            edit_message_caption=AsyncMock(),
        )
        context = SimpleNamespace(
            application=SimpleNamespace(
                bot_data={"guide": self.make_guide()}
            )
        )
        with patch.object(bot, "CUSTOM_IMAGES_DIR", custom_images):
            asyncio.run(bot.handle_callback(SimpleNamespace(callback_query=query), context))
        query.message.reply_photo.assert_awaited_once()
        caption = query.message.reply_photo.await_args.kwargs["caption"]
        self.assertIn("not confirmed", caption)
        self.assertGreaterEqual(query.message.reply_text.await_count, 1)

    def test_rewards_issue_sends_quota_illustration_with_policy_caveat(self):
        custom_images = self.root / "custom_images"
        custom_images.mkdir()
        (custom_images / "reward_quota.jpg").write_bytes(b"test image")
        query = SimpleNamespace(
            data="issue:rewards",
            message=SimpleNamespace(
                photo=[],
                reply_text=AsyncMock(),
                reply_photo=AsyncMock(),
            ),
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
            edit_message_caption=AsyncMock(),
        )
        context = SimpleNamespace(
            application=SimpleNamespace(
                bot_data={"guide": self.make_guide()}
            )
        )
        with patch.object(bot, "CUSTOM_IMAGES_DIR", custom_images):
            asyncio.run(
                bot.handle_callback(SimpleNamespace(callback_query=query), context)
            )
        query.message.reply_photo.assert_awaited_once()
        self.assertIn(
            "not its unit or calculation",
            query.message.reply_photo.await_args.kwargs["caption"],
        )

    def test_wallet_menu_sends_supported_image_and_only_three_buttons(self):
        query = SimpleNamespace(
            data="menu:wallets",
            message=SimpleNamespace(
                photo=[],
                reply_photo=AsyncMock(),
                reply_text=AsyncMock(),
            ),
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
            edit_message_caption=AsyncMock(),
            edit_message_reply_markup=AsyncMock(),
        )
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={"guide": self.make_guide()})
        )
        with patch.object(
            bot,
            "CUSTOM_IMAGES_DIR",
            Path(__file__).resolve().parents[1] / "custom_images",
        ):
            asyncio.run(
                bot.handle_callback(
                    SimpleNamespace(callback_query=query),
                    context,
                )
            )
        query.message.reply_photo.assert_awaited_once()
        self.assertEqual(
            query.message.reply_photo.await_args.kwargs["photo"].filename,
            "wallets_payin.jpg",
        )
        self.assertEqual(
            query.message.reply_photo.await_args.kwargs["caption"],
            "💰 SUPPORTED WALLETS",
        )
        markup = query.message.reply_photo.await_args.kwargs["reply_markup"]
        self.assertEqual(
            [button.callback_data for row in markup.inline_keyboard for button in row],
            ["wallet:payin", "wallet:payout", "nav:menu"],
        )

    def test_payin_and_payout_update_caption_without_replacing_wallet_image(self):
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={"guide": self.make_guide()})
        )
        for action, expected_title in (
            ("wallet:payin", "PAYIN"),
            ("wallet:payout", "PAYOUT"),
        ):
            with self.subTest(action=action), patch.object(
                bot,
                "CUSTOM_IMAGES_DIR",
                Path(__file__).resolve().parents[1] / "custom_images",
            ):
                message = SimpleNamespace(
                    photo=[object()],
                    reply_photo=AsyncMock(),
                    reply_text=AsyncMock(),
                )
                query = SimpleNamespace(
                    data=action,
                    message=message,
                    answer=AsyncMock(),
                    edit_message_media=AsyncMock(),
                    edit_message_text=AsyncMock(),
                    edit_message_caption=AsyncMock(),
                    edit_message_reply_markup=AsyncMock(),
                )
                asyncio.run(
                    bot.handle_callback(
                        SimpleNamespace(callback_query=query),
                        context,
                    )
                )
                query.edit_message_media.assert_awaited_once()
                self.assertEqual(query.message.reply_photo.await_count, 0)
                media = query.edit_message_media.await_args.kwargs["media"]
                self.assertEqual(media.media.filename, "wallets_payin.jpg")
                self.assertIn(expected_title, media.caption)
                for wallet in (
                    "PhonePe",
                    "Paytm",
                    "MobiKwik",
                    "Freecharge",
                    "Google Pay (GPay)",
                    "PhonePe Business",
                    "Paytm Business",
                ):
                    self.assertIn(wallet, media.caption)
                self.assertIn("does not confirm Google Pay", media.caption)
                markup = query.edit_message_media.await_args.kwargs["reply_markup"]
                self.assertEqual(
                    [button.callback_data for row in markup.inline_keyboard for button in row],
                    ["wallet:payin", "wallet:payout", "nav:menu"],
                )


if __name__ == "__main__":
    unittest.main()
