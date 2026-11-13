import asyncio
import shutil
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock, patch
import uuid

import pymupdf
from telegram.error import BadRequest, Conflict
from telegram.ext import CommandHandler, MessageHandler

import bot
from guide import Guide
from run_bot_watch import watched_files


class BotTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent / f".bot-test-{uuid.uuid4().hex}"
        self.root.mkdir()
        self.supabase_url_patch = patch.object(bot, "SUPABASE_URL", "")
        self.supabase_key_patch = patch.object(bot, "SUPABASE_SECRET_KEY", "")
        self.supabase_url_patch.start()
        self.supabase_key_patch.start()
        bot.PHOTO_FILE_IDS.clear()
        self.knowledge_path = self.root / "knowledge_base.md"
        self.knowledge_path.write_text(
            "# Wallets\nPhonePe and Paytm are supported wallets.\n\n"
            "# Payments\nCheck the recipient, exact amount, and expiry before paying.\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self.supabase_url_patch.stop()
        self.supabase_key_patch.stop()
        shutil.rmtree(self.root)

    def test_http_request_logs_do_not_emit_tokens_at_info_level(self):
        self.assertGreaterEqual(
            bot.logging.getLogger("httpx").getEffectiveLevel(),
            bot.logging.WARNING,
        )
        self.assertGreaterEqual(
            bot.logging.getLogger("httpcore").getEffectiveLevel(),
            bot.logging.WARNING,
        )

    def test_conflict_error_handler_stops_polling_cleanly(self):
        application = SimpleNamespace(stop_running=Mock())
        context = SimpleNamespace(
            error=Conflict("terminated by other getUpdates request"),
            application=application,
        )
        asyncio.run(bot.error_handler(None, context))
        application.stop_running.assert_called_once_with()

    def test_instance_lock_rejects_second_local_process(self):
        token = "test-token-for-instance-lock"
        previous_handle = bot._INSTANCE_LOCK_HANDLE
        bot._INSTANCE_LOCK_HANDLE = None
        with patch.object(bot.tempfile, "gettempdir", return_value=str(self.root)):
            try:
                bot.acquire_instance_lock(token)
                with self.assertRaisesRegex(RuntimeError, "already running"):
                    bot.acquire_instance_lock(token)
            finally:
                if bot._INSTANCE_LOCK_HANDLE:
                    bot._INSTANCE_LOCK_HANDLE.close()
                bot._INSTANCE_LOCK_HANDLE = previous_handle

    def make_guide(self):
        return Guide(
            self.root / "missing.pdf",
            self.knowledge_path,
            self.root / "images",
        )

    def test_default_guide_paths_are_anchored_to_the_project(self):
        for path, environment_key, default in (
            (bot.GUIDE_PDF_PATH, "GUIDE_PDF_PATH", "I_PAY_User_Guide.pdf"),
            (bot.GUIDE_IMAGE_DIR, "GUIDE_IMAGE_DIR", "guide_pages"),
            (bot.KNOWLEDGE_PATH, "KNOWLEDGE_PATH", "knowledge_base.md"),
        ):
            self.assertEqual(path, bot._asset_path(bot.os.getenv(environment_key, default)))

    def test_referral_banner_asset_exists(self):
        self.assertTrue(bot.REFERRAL_IMAGE_PATH.is_file())

    def test_problem_resolutions_rejects_non_object_json(self):
        resolutions_path = self.root / "problem_resolutions.json"
        resolutions_path.write_text("[]", encoding="utf-8")
        with patch.dict("os.environ", {"PROBLEM_RESOLUTIONS_PATH": str(resolutions_path)}):
            self.assertEqual(bot._load_problem_resolutions(), {})

    def test_activity_handler_ignores_callback_query_messages(self):
        store = SimpleNamespace(record_activity=Mock())
        callback_message = SimpleNamespace(message_id=55)
        update = SimpleNamespace(
            message=None,
            effective_message=callback_message,
            effective_chat=SimpleNamespace(id=-1001, type=bot.ChatType.SUPERGROUP),
            effective_user=SimpleNamespace(id=12),
        )
        context = SimpleNamespace()
        with patch.object(bot, "_question_store", return_value=store):
            asyncio.run(bot.activity_handler(update, context))
        store.record_activity.assert_not_called()

        update.message = SimpleNamespace(message_id=56, date=None)
        with patch.object(bot, "_question_store", return_value=store):
            asyncio.run(bot.activity_handler(update, context))
        store.record_activity.assert_called_once()
        self.assertEqual(store.record_activity.call_args.kwargs["message_id"], 56)

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
                bot.notice_keyboard(),
                bot.new_user_keyboard(),
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
             "menu:customer_support", "menu:start", "menu:new_user", "nav:menu"} <= callback_actions
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

    def test_new_user_images_exist(self):
        self.assertTrue(all(path.is_file() for path in (
            bot.HOW_IPAY_WORKS_IMAGE_PATH,
            bot.HOW_TO_PLACE_ORDER_IMAGE_PATH,
            bot.GUIDELINES_IMAGE_PATH,
            bot.SUGGESTION_IMAGE_PATH,
        )))

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
            button for button in buttons if button.text.endswith("Group Support")
        )
        self.assertTrue(customer_button.text.endswith("Customer Support"))
        self.assertEqual(group_button.url, "https://t.me/ipayvip_1")
        channel_button = next(button for button in buttons if button.text == "📢 Channel Support")
        official_group_button = next(button for button in buttons if button.text == "👥 Official Group")
        self.assertEqual(channel_button.url, "https://t.me/Ipay_Official_Channel")
        self.assertEqual(official_group_button.url, "https://t.me/+wtcrw4UYSDViMjA1")

    def test_customer_support_contacts_also_offer_group_support(self):
        keyboard = bot.support_keyboard()
        customer_buttons = keyboard.inline_keyboard[0]
        third_customer_button, channel_button = keyboard.inline_keyboard[1]
        group_button, official_group_button = keyboard.inline_keyboard[2]
        self.assertEqual(
            [button.text for button in customer_buttons],
            [f"Contact {bot.SUPPORT_1}", f"{bot.ICON_MESSAGE} Contact {bot.SUPPORT_2}"],
        )
        self.assertEqual(
            third_customer_button.text,
            f"{bot.ICON_MESSAGE} Contact {bot.SUPPORT_3}",
        )
        self.assertEqual(third_customer_button.url, "https://t.me/Ipay811")
        self.assertEqual(channel_button.text, "📢 Official Channel")
        self.assertEqual(channel_button.url, "https://t.me/Ipay_Official_Channel")
        self.assertEqual(group_button.text, "👥 Group Support")
        self.assertEqual(group_button.url, "https://t.me/ipayvip_1")
        self.assertEqual(official_group_button.text, "👥 Official Group")
        self.assertEqual(official_group_button.url, "https://t.me/+wtcrw4UYSDViMjA1")

    def test_wallet_menu_has_only_payin_payout_and_back_buttons(self):
        buttons = [
            button
            for row in bot.wallets_keyboard().inline_keyboard
            for button in row
        ]
        self.assertEqual(
            [button.callback_data for button in buttons],
            [
                "wallet:payin",
                "wallet:payout",
                "nav:back",
            ],
        )

    def test_submenu_back_buttons_except_security_and_start(self):
        keyboards = [
            bot.wallets_keyboard(),
            bot.issues_keyboard(),
            bot.support_keyboard(),
            bot.support_options_keyboard(),
            bot.referral_keyboard(),
            bot.notice_keyboard(),
        ]
        for keyboard in keyboards:
            with self.subTest(keyboard=keyboard):
                buttons = [
                    button
                    for row in keyboard.inline_keyboard
                    for button in row
                ]
                self.assertIn("nav:back", [button.callback_data for button in buttons])

        for keyboard in (bot.security_keyboard(), bot.start_keyboard()):
            buttons = [
                button
                for row in keyboard.inline_keyboard
                for button in row
            ]
            self.assertIn("nav:menu", [button.callback_data for button in buttons])
            self.assertNotIn("nav:back", [button.callback_data for button in buttons])

    def test_issue_menu_hides_reward_account_and_other_payment_buttons(self):
        buttons = [
            button
            for row in bot.issues_keyboard().inline_keyboard
            for button in row
        ]
        callbacks = [button.callback_data for button in buttons]
        self.assertNotIn("issue:rewards", callbacks)
        self.assertNotIn("issue:account", callbacks)
        self.assertNotIn("issue:other", callbacks)
        issue_rows = bot.issues_keyboard().inline_keyboard
        self.assertEqual(
            [button.text for button in issue_rows[0]],
            [f"{bot.ICON_CHECK} Deposit", f"{bot.ICON_WITHDRAWAL} Withdrawal"],
        )
        self.assertEqual(
            [button.callback_data for button in issue_rows[1]],
            ["issue:guide", "issue:commission"],
        )
        self.assertEqual(
            [button.text for button in issue_rows[1]],
            [f"{bot.ICON_GUIDE} Guide", f"{bot.ICON_COMMISSION} Commission"],
        )

    def test_issue_guide_button_sends_pdf_as_downloadable_document(self):
        guide_pdf = self.root / "I_PAY_User_Guide.pdf"
        guide_pdf.write_bytes(b"%PDF-1.4 test guide")
        message = SimpleNamespace(
            reply_document=AsyncMock(),
            reply_text=AsyncMock(),
        )
        query = SimpleNamespace(
            data="issue:guide",
            message=message,
            answer=AsyncMock(),
        )
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={})
        )
        with patch.object(bot, "GUIDE_PDF_PATH", guide_pdf):
            asyncio.run(
                bot.handle_callback(SimpleNamespace(callback_query=query), context)
            )
        query.answer.assert_awaited_once()
        message.reply_document.assert_awaited_once()
        self.assertEqual(
            message.reply_document.await_args.kwargs["filename"],
            "I_PAY_User_Guide.pdf",
        )
        self.assertEqual(
            message.reply_document.await_args.kwargs["caption"],
            "iPay User Guide",
        )
        message.reply_text.assert_not_awaited()

    def test_issues_command_displays_issue_menu_image(self):
        message = SimpleNamespace(reply_photo=AsyncMock(), reply_text=AsyncMock())
        image_path = self.root / "issues_home.png"
        image_path.write_bytes(b"test image")
        with patch.object(bot, "ISSUES_IMAGE_PATH", image_path):
            asyncio.run(
                bot.issues_command(
                    SimpleNamespace(effective_message=message),
                    SimpleNamespace(),
                )
            )
        message.reply_photo.assert_awaited_once()
        self.assertIn(
            "Identify your issue and click the button below",
            message.reply_photo.await_args.kwargs["caption"],
        )
        buttons = message.reply_photo.await_args.kwargs["reply_markup"].inline_keyboard
        self.assertNotIn(
            "issue:account",
            [button.callback_data for row in buttons for button in row],
        )
        self.assertNotIn(
            "issue:other",
            [button.callback_data for row in buttons for button in row],
        )
        self.assertIn(
            "nav:back",
            [button.callback_data for row in buttons for button in row],
        )

    def test_public_and_admin_commands_are_registered(self):
        application = bot.build_application(
            "dummy-telegram-token-for-tests",
            guide=self.make_guide(),
        )
        self.assertEqual(
            application.update_processor.max_concurrent_updates,
            8,
        )
        self.assertTrue(application.error_handlers)
        commands = {
            command
            for handler in application.handlers[0]
            if isinstance(handler, CommandHandler)
            for command in handler.commands
        }
        self.assertEqual(
            commands,
            {"start", *bot.BOOST_COMMANDS, "questions", "qsearch", "qstatus", "qnote", "qresolve", "qshow", "qhistory", "qassign", "dailyreport", "unanswered"},
        )
        boost_handlers = [
            handler
            for handler in application.handlers[0]
            if isinstance(handler, CommandHandler)
            and set(handler.commands) & set(bot.BOOST_COMMANDS)
        ]
        self.assertEqual(len(boost_handlers), 1)
        self.assertEqual(boost_handlers[0].callback, bot.boost_command)
        caption_handlers = [
            handler
            for handler in application.handlers[0]
            if isinstance(handler, MessageHandler)
        ]
        self.assertEqual(len(caption_handlers), 1)
        self.assertEqual(caption_handlers[0].callback, bot.boost_caption_handler)
        self.assertEqual(len(application.handlers[0]), 14)
        self.assertEqual(application.handlers[1][0].callback, bot.ai_reply)
        self.assertEqual(application.handlers[-1][0].callback, bot.activity_handler)

    def test_only_start_and_boost_are_published_to_telegram(self):
        application = SimpleNamespace(
            bot=SimpleNamespace(set_my_commands=AsyncMock())
        )
        asyncio.run(bot._post_init(application))
        commands = application.bot.set_my_commands.await_args.args[0]
        self.assertEqual(
            [command.command for command in commands],
            ["start", "boost"],
        )

    def test_every_callback_action_runs(self):
        guide = self.make_guide()
        custom_images = self.root / "custom_images"
        custom_images.mkdir()
        for filename in (
            "supported_wallets.png",
            "security.jpg",
            "commission.jpg",
            "deposit_problem.jpg",
            "withdrawal_delay.jpg",
            "upi_locked.jpg",
            "order_locked.jpg",
            "reward_quota.jpg",
            "how_ipay_works.jpg",
            "how_to_place_order.jpg",
            "smart_guidelines.jpg",
            "security_scammer_reminder.jpg",
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
                patch.object(bot, "WALLET_IMAGE_PATH", custom_images / "supported_wallets.png"),
                patch.object(bot, "HOW_IPAY_WORKS_IMAGE_PATH", custom_images / "how_ipay_works.jpg"),
                patch.object(bot, "HOW_TO_PLACE_ORDER_IMAGE_PATH", custom_images / "how_to_place_order.jpg"),
                patch.object(bot, "GUIDELINES_IMAGE_PATH", custom_images / "smart_guidelines.jpg"),
                patch.object(bot, "SUGGESTION_IMAGE_PATH", custom_images / "security_scammer_reminder.jpg"),
            ):
                message = SimpleNamespace(
                    photo=[],
                    reply_text=AsyncMock(),
                    reply_photo=AsyncMock(),
                    reply_document=AsyncMock(),
                    reply_media_group=AsyncMock(),
                )
                query = SimpleNamespace(
                    data=action,
                    message=message,
                    answer=AsyncMock(),
                    delete_message=AsyncMock(),
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
                    or query.edit_message_media.await_count
                    or message.reply_photo.await_count
                    or message.reply_document.await_count
                    or message.reply_text.await_count
                    or message.reply_media_group.await_count
                )

    def test_every_command_handler_returns_a_response(self):
        guide = self.make_guide()
        message = SimpleNamespace(
            text="/test",
            reply_text=AsyncMock(),
            reply_photo=AsyncMock(),
        )
        update = SimpleNamespace(
            effective_message=message,
            effective_user=SimpleNamespace(id=101, username="testuser", first_name="Test"),
            effective_chat=SimpleNamespace(type=bot.ChatType.PRIVATE, id=101),
        )
        context = SimpleNamespace(
            args=[],
            application=SimpleNamespace(bot_data={"guide": guide})
        )
        commands = [
            bot.start,
            bot.help_command,
            bot.boost_command,
            bot.uid_command,
            bot.tag_command,
            bot.notice_command,
            bot.faq_command,
            bot.agent_command,
            bot.report_command,
            bot.urgent_command,
            bot.ask_command,
            bot.wallets_command,
            bot.relink_command,
            bot.wallet_status_command,
            bot.troubleshoot_command,
            bot.new_wallets_command,
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
            bot.ai_control_command,
            bot.language_control_command,
            bot.unanswered_command,
        ]
        with patch.object(bot, "DB_PATH", self.root / "commands.sqlite3"):
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

    def test_start_displays_home_image_and_welcome_text(self):
        image_path = (
            Path(__file__).resolve().parents[1]
            / "custom_images"
            / "welcome_new_user.jpg"
        )
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
        message.reply_text.assert_not_awaited()
        text = message.reply_photo.await_args.kwargs["caption"]
        self.assertIn("WELCOME TO iPAY", text)
        ET.fromstring(f"<root>{text}</root>")
        self.assertNotIn("<b>", text)
        self.assertNotIn("</b>", text)
        self.assertNotIn("![", text)
        self.assertNotIn("<tg-emoji", text)
        self.assertTrue(text.startswith("WELCOME TO iPAY ⚡\n\n"))
        self.assertEqual(
            text,
            "WELCOME TO iPAY ⚡\n\n"
            "📈 Up to 4% commission on orders\n"
            "🎯 Rewards on selected activities\n"
            "💼 Easy access to withdrawal support\n"
            "🪧 Scalable earning opportunities\n\n"
            "🏛️ Choose iPAY, choose a smarter path forward.",
        )
        self.assertNotIn("Your guide to wallets, payments, and support:", text)
        self.assertIn(
            "🏛️ Choose iPAY, choose a smarter path forward.",
            text,
        )
        self.assertNotIn("Hello @", text)
        self.assertNotIn("How was your day", text)
        self.assertIn("reply_markup", message.reply_photo.await_args.kwargs)
        start_button = bot.main_keyboard().inline_keyboard[-1][0]
        self.assertEqual(start_button.text, f"{bot.ICON_ARROW} START NOW")

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
            / "welcome_new_user.jpg"
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
            delete_message=AsyncMock(),
            edit_message_media=AsyncMock(),
        )
        update = SimpleNamespace(callback_query=query)
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={"guide": self.make_guide()})
        )
        with patch.object(bot, "WELCOME_IMAGE_PATH", image_path):
            asyncio.run(bot.handle_callback(update, context))
        query.edit_message_media.assert_not_awaited()
        message.reply_photo.assert_awaited_once()
        query.delete_message.assert_awaited_once()
        sent_photo = message.reply_photo.await_args.kwargs
        self.assertEqual(sent_photo["photo"].filename, "welcome_new_user.jpg")
        self.assertIn("WELCOME TO iPAY", sent_photo["caption"])

    def test_main_menu_from_text_replaces_message_with_home_image(self):
        image_path = (
            Path(__file__).resolve().parents[1]
            / "custom_images"
            / "welcome_new_user.jpg"
        )
        message = SimpleNamespace(
            photo=[],
            reply_photo=AsyncMock(),
            reply_text=AsyncMock(),
        )
        query = SimpleNamespace(
            data="nav:menu",
            message=message,
            answer=AsyncMock(),
            delete_message=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={"guide": self.make_guide()})
        )
        with patch.object(bot, "WELCOME_IMAGE_PATH", image_path):
            asyncio.run(bot.handle_callback(SimpleNamespace(callback_query=query), context))
        message.reply_photo.assert_awaited_once()
        self.assertEqual(
            message.reply_photo.await_args.kwargs["photo"].filename,
            "welcome_new_user.jpg",
        )
        query.delete_message.assert_awaited_once()
        message.reply_text.assert_not_awaited()

    def test_support_callback_displays_support_image(self):
        image_path = (
            Path(__file__).resolve().parents[1]
            / "custom_images"
            / "support_home_compressed.jpg"
        )
        message = SimpleNamespace(
            photo=[object()],
            reply_photo=AsyncMock(),
            reply_text=AsyncMock(),
            edit_message_reply_markup=AsyncMock(),
        )
        query = SimpleNamespace(
            data="menu:support",
            message=message,
            answer=AsyncMock(),
            delete_message=AsyncMock(),
            edit_message_media=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={"guide": self.make_guide()})
        )
        with patch.object(bot, "SUPPORT_IMAGE_PATH", image_path):
            asyncio.run(
                bot.handle_callback(SimpleNamespace(callback_query=query), context)
            )
        query.edit_message_media.assert_not_awaited()
        message.reply_photo.assert_awaited_once()
        query.delete_message.assert_awaited_once()
        sent_photo = message.reply_photo.await_args.kwargs
        self.assertEqual(sent_photo["photo"].filename, "support_home_compressed.jpg")
        self.assertNotIn("caption", sent_photo)
        markup = sent_photo["reply_markup"]
        self.assertIn(
            "menu:customer_support",
            {
                button.callback_data
                for row in markup.inline_keyboard
                for button in row
            },
        )

    def test_security_callback_displays_security_image_and_guidance(self):
        image_path = (
            Path(__file__).resolve().parents[1]
            / "custom_images"
            / "security.jpg"
        )
        message = SimpleNamespace(
            photo=[object()],
            reply_photo=AsyncMock(),
            reply_text=AsyncMock(),
            edit_message_reply_markup=AsyncMock(),
        )
        query = SimpleNamespace(
            data="menu:security",
            message=message,
            answer=AsyncMock(),
            delete_message=AsyncMock(),
            edit_message_media=AsyncMock(),
            edit_message_text=AsyncMock(),
            edit_message_reply_markup=AsyncMock(),
        )
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={"guide": self.make_guide()})
        )
        with patch.object(bot, "SECURITY_IMAGE_PATH", image_path):
            asyncio.run(
                bot.handle_callback(SimpleNamespace(callback_query=query), context)
            )
        query.edit_message_media.assert_not_awaited()
        message.reply_photo.assert_awaited_once()
        query.delete_message.assert_awaited_once()
        sent_photo = message.reply_photo.await_args.kwargs
        self.assertEqual(sent_photo["photo"].filename, "security.jpg")
        self.assertEqual(sent_photo["caption"], bot.SECURITY_ALERT_TEXT)
        self.assertLessEqual(len(sent_photo["caption"]), 1024)
        markup = sent_photo["reply_markup"]
        self.assertIn(
            "menu:customer_support",
            {
                button.callback_data
                for row in markup.inline_keyboard
                for button in row
            },
        )
        self.assertNotIn(
            "Group Support",
            [
                button.text
                for row in markup.inline_keyboard
                for button in row
            ],
        )

    def test_support_and_security_callbacks_show_images_from_text_menus(self):
        project_root = Path(__file__).resolve().parents[1]
        cases = (
            ("menu:support", "SUPPORT_IMAGE_PATH", "support_home_compressed.jpg"),
            ("menu:security", "SECURITY_IMAGE_PATH", "security.jpg"),
        )
        for action, path_setting, image_name in cases:
            with self.subTest(action=action):
                message = SimpleNamespace(
                    photo=[],
                    reply_photo=AsyncMock(),
                    reply_text=AsyncMock(),
                )
                query = SimpleNamespace(
                    data=action,
                    message=message,
                    answer=AsyncMock(),
                    delete_message=AsyncMock(),
                    edit_message_text=AsyncMock(),
                )
                context = SimpleNamespace(
                    application=SimpleNamespace(
                        bot_data={"guide": self.make_guide()}
                    )
                )
                with patch.object(
                    bot,
                    path_setting,
                    project_root / "custom_images" / image_name,
                ):
                    asyncio.run(
                        bot.handle_callback(
                            SimpleNamespace(callback_query=query),
                            context,
                        )
                    )
                message.reply_photo.assert_awaited_once()
                self.assertEqual(
                    message.reply_photo.await_args.kwargs["photo"].filename,
                    image_name,
                )
                if action == "menu:security":
                    self.assertEqual(
                        message.reply_photo.await_args.kwargs["caption"],
                        bot.SECURITY_ALERT_TEXT,
                    )
                else:
                    self.assertNotIn(
                        "caption",
                        message.reply_photo.await_args.kwargs,
                    )
                query.delete_message.assert_awaited_once()
                message.reply_text.assert_not_awaited()

    def test_callback_image_reuses_telegram_file_id_for_replacement_message(self):
        image_path = self.root / "menu.jpg"
        image_path.write_bytes(b"test image")

        first_message = SimpleNamespace(
            photo=[],
            reply_photo=AsyncMock(
                return_value=SimpleNamespace(
                    photo=[SimpleNamespace(file_id="cached-photo-id")]
                )
            ),
        )
        first_query = SimpleNamespace(
            message=first_message,
            delete_message=AsyncMock(),
        )
        first_update = SimpleNamespace(callback_query=first_query)
        asyncio.run(
            bot._replace_callback_image(
                first_update,
                image_path,
                "First menu",
                bot.main_keyboard(),
            )
        )
        first_media = first_message.reply_photo.await_args.kwargs["photo"]
        self.assertIsInstance(first_media, bot.InputFile)
        first_query.delete_message.assert_awaited_once()

        second_message = SimpleNamespace(
            photo=[],
            reply_photo=AsyncMock(
                return_value=SimpleNamespace(
                    photo=[SimpleNamespace(file_id="cached-photo-id")]
                )
            ),
        )
        second_query = SimpleNamespace(
            message=second_message,
            delete_message=AsyncMock(),
        )
        second_update = SimpleNamespace(callback_query=second_query)
        asyncio.run(
            bot._replace_callback_image(
                second_update,
                image_path,
                "Second menu",
                bot.main_keyboard(),
            )
        )
        second_photo = second_message.reply_photo.await_args.kwargs["photo"]
        self.assertEqual(second_photo, "cached-photo-id")
        second_query.delete_message.assert_awaited_once()

    def test_expired_callback_answer_does_not_prevent_menu_image(self):
        image_path = (
            Path(__file__).resolve().parents[1]
            / "custom_images"
            / "support_home_compressed.jpg"
        )
        message = SimpleNamespace(
            photo=[],
            reply_photo=AsyncMock(),
            reply_text=AsyncMock(),
        )
        query = SimpleNamespace(
            data="menu:support",
            message=message,
            answer=AsyncMock(
                side_effect=BadRequest(
                    "Query is too old and response timeout expired"
                )
            ),
            delete_message=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={"guide": self.make_guide()})
        )
        with patch.object(bot, "SUPPORT_IMAGE_PATH", image_path):
            asyncio.run(
                bot.handle_callback(SimpleNamespace(callback_query=query), context)
            )
        query.answer.assert_awaited_once()
        message.reply_photo.assert_awaited_once()
        message.reply_text.assert_not_awaited()

    def test_security_command_sends_security_image(self):
        message = SimpleNamespace(
            reply_photo=AsyncMock(),
            reply_text=AsyncMock(),
        )
        with patch.object(
            bot,
            "SECURITY_IMAGE_PATH",
            Path(__file__).resolve().parents[1]
            / "custom_images"
            / "security.jpg",
        ):
            asyncio.run(
                bot._send_security(message)
            )
        message.reply_photo.assert_awaited_once()
        self.assertEqual(
            message.reply_photo.await_args.kwargs["caption"],
            bot.SECURITY_ALERT_TEXT,
        )
        keyboard = message.reply_photo.await_args.kwargs["reply_markup"]
        self.assertNotIn(
            "Group Support",
            [
                button.text
                for row in keyboard.inline_keyboard
                for button in row
            ],
        )

    def test_support_command_sends_support_image_only(self):
        message = SimpleNamespace(
            reply_photo=AsyncMock(),
            reply_text=AsyncMock(),
        )
        with patch.object(
            bot,
            "SUPPORT_IMAGE_PATH",
            Path(__file__).resolve().parents[1]
            / "custom_images"
            / "support_home_compressed.jpg",
        ):
            asyncio.run(bot._send_support(message))
        message.reply_photo.assert_awaited_once()
        self.assertNotIn("caption", message.reply_photo.await_args.kwargs)
        message.reply_text.assert_not_awaited()
        message.reply_text.assert_not_awaited()

    def test_issues_menu_replaces_home_image_with_customer_service_image(self):
        image_path = (
            Path(__file__).resolve().parents[1]
            / "custom_images"
            / "customer_service.png"
        )
        message = SimpleNamespace(
            photo=[object()],
            reply_photo=AsyncMock(),
            reply_text=AsyncMock(),
        )
        query = SimpleNamespace(
            data="menu:issues",
            message=message,
            answer=AsyncMock(),
            delete_message=AsyncMock(),
            edit_message_media=AsyncMock(),
            edit_message_caption=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={"guide": self.make_guide()})
        )
        with patch.object(bot, "ISSUES_IMAGE_PATH", image_path):
            asyncio.run(
                bot.handle_callback(SimpleNamespace(callback_query=query), context)
            )
        query.edit_message_media.assert_not_awaited()
        message.reply_photo.assert_awaited_once()
        query.delete_message.assert_awaited_once()
        media_kwargs = message.reply_photo.await_args.kwargs
        self.assertEqual(media_kwargs["photo"].filename, "customer_service.png")
        self.assertIn(
            "Identify your issue and click the button below",
            media_kwargs["caption"],
        )
        markup = media_kwargs["reply_markup"]
        self.assertIn(
            "issue:withdrawal",
            {
                button.callback_data
                for row in markup.inline_keyboard
                for button in row
            },
        )

    def test_issues_callback_falls_back_to_text_when_image_upload_fails(self):
        image_path = self.root / "issues.jpg"
        image_path.write_bytes(b"test image")
        message = SimpleNamespace(
            reply_photo=AsyncMock(side_effect=BadRequest("image upload failed")),
            reply_text=AsyncMock(),
        )
        query = SimpleNamespace(
            message=message,
            delete_message=AsyncMock(),
        )
        with patch.object(bot, "ISSUES_IMAGE_PATH", image_path):
            asyncio.run(
                bot._show_issues_menu(SimpleNamespace(callback_query=query))
            )
        message.reply_text.assert_awaited_once()
        self.assertIn(
            "Issues image could not be sent",
            message.reply_text.await_args.args[0],
        )

    def test_customer_support_callback_displays_customer_service_image(self):
        image_path = self.root / "customer_service.png"
        image_path.write_bytes(b"test image")
        query = SimpleNamespace(
            data="menu:customer_support",
            message=SimpleNamespace(
                photo=[object()],
                reply_photo=AsyncMock(),
                reply_text=AsyncMock(),
            ),
            answer=AsyncMock(),
            delete_message=AsyncMock(),
            edit_message_media=AsyncMock(),
            edit_message_caption=AsyncMock(),
        )
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={"guide": self.make_guide()})
        )
        with patch.object(bot, "CUSTOMER_SUPPORT_IMAGE_PATH", image_path):
            asyncio.run(
                bot.handle_callback(SimpleNamespace(callback_query=query), context)
            )
        query.message.reply_photo.assert_awaited_once()
        query.message.reply_text.assert_not_awaited()
        query.delete_message.assert_awaited_once()
        sent_photo = query.message.reply_photo.await_args.kwargs
        self.assertEqual(sent_photo["photo"].filename, "customer_service.png")
        self.assertEqual(
            sent_photo["caption"],
            "Choose a customer support contact:",
        )
        self.assertIn("reply_markup", sent_photo)
        query.edit_message_media.assert_not_awaited()

    def test_customer_support_callback_displays_image_from_text_message(self):
        image_path = self.root / "customer_service.png"
        image_path.write_bytes(b"test image")
        query = SimpleNamespace(
            data="menu:customer_support",
            message=SimpleNamespace(
                photo=[],
                reply_photo=AsyncMock(),
                reply_text=AsyncMock(),
            ),
            answer=AsyncMock(),
            delete_message=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={"guide": self.make_guide()})
        )
        with patch.object(bot, "CUSTOMER_SUPPORT_IMAGE_PATH", image_path):
            asyncio.run(
                bot.handle_callback(SimpleNamespace(callback_query=query), context)
            )
        query.answer.assert_awaited_once()
        query.message.reply_photo.assert_awaited_once()
        query.message.reply_text.assert_not_awaited()
        query.delete_message.assert_awaited_once()
        sent_photo = query.message.reply_photo.await_args.kwargs
        self.assertEqual(sent_photo["photo"].filename, "customer_service.png")
        self.assertEqual(
            sent_photo["caption"],
            "Choose a customer support contact:",
        )
        keyboard = sent_photo["reply_markup"]
        customer_buttons = [
            button
            for row in keyboard.inline_keyboard
            for button in row
            if button.url
        ]
        self.assertEqual(
            [button.url for button in customer_buttons],
            [
                "https://t.me/Ipay800",
                "https://t.me/Roas668",
                "https://t.me/Ipay811",
                "https://t.me/Ipay_Official_Channel",
                bot.GROUP_SUPPORT_URL,
                bot.OFFICIAL_GROUP_URL,
            ],
        )

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

    def test_supplied_issue_images_keep_full_resolution(self):
        project_root = Path(__file__).resolve().parents[1]
        for filename in (
            "deposit_problem.jpg",
            "withdrawal_delay.jpg",
            "upi_locked.jpg",
        ):
            with self.subTest(filename=filename):
                image = pymupdf.Pixmap(
                    str(project_root / "custom_images" / filename)
                )
                self.assertGreaterEqual(image.width, 1280)
                self.assertGreaterEqual(image.height, 720)

    def test_active_menu_images_are_compressed_for_fast_uploads(self):
        project_root = Path(__file__).resolve().parents[1]
        expected_dimensions = {
            "deposit_problem.jpg": (1365, 768),
            "withdrawal_delay.jpg": (1365, 768),
            "commission.jpg": (1152, 768),
            "upi_locked.jpg": (1365, 768),
            "issues_home_compressed.jpg": (1365, 768),
            "welcome_home_compressed.jpg": (1367, 768),
            "support_home_compressed.jpg": (1365, 768),
        }
        for filename, dimensions in expected_dimensions.items():
            with self.subTest(filename=filename):
                image_path = project_root / "custom_images" / filename
                image = pymupdf.Pixmap(str(image_path))
                self.assertEqual((image.width, image.height), dimensions)
                self.assertLess(image_path.stat().st_size, 300_000)

    def test_commission_image_keeps_supplied_resolution(self):
        image = pymupdf.Pixmap(
            str(Path(__file__).resolve().parents[1] / "custom_images" / "commission.jpg")
        )
        self.assertGreaterEqual(image.width, 1100)
        self.assertGreaterEqual(image.height, 700)

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

    def test_multilingual_and_transliterated_withdrawal_retrieval(self):
        project_root = Path(__file__).resolve().parents[1]
        guide = Guide(
            project_root / "I_PAY_User_Guide.pdf",
            self.knowledge_path,
            self.root / "multilingual-images",
        )
        questions = [
            "How do I withdraw money?",
            "Withdrawal kaise kare?",
            "पैसे कैसे निकालें?",
            "డబ్బు ఎలా తీసుకోవాలి?",
            "பணம் எடுப்பது எப்படி?",
            "ಹಣ ಹಿಂಪಡೆಯುವುದು ಹೇಗೆ?",
            "പണം പിൻവലിക്കുന്നത് എങ്ങനെ?",
            "টাকা কীভাবে তুলব?",
            "पैसे काढणे कसे?",
            "પૈસા ઉપાડવા કેવી રીતે?",
            "Withdraw ela cheyali?",
            "My withdrwal is pendng",
        ]
        for question in questions:
            with self.subTest(question=question):
                pages = guide.retrieve(question, top_k=2)
                self.assertTrue(pages, f"No guide page retrieved for {question!r}")
                self.assertIn(8, [page.page_number for page in pages])

    def test_admin_can_toggle_global_ai_group_ai_and_languages(self):
        db_path = self.root / "admin-settings.sqlite3"
        message = SimpleNamespace(reply_text=AsyncMock())
        user = SimpleNamespace(id=98765)
        chat = SimpleNamespace(type=bot.ChatType.SUPERGROUP, id=-42)
        update = SimpleNamespace(
            effective_message=message,
            effective_user=user,
            effective_chat=chat,
        )
        with (
            patch.object(bot, "DB_PATH", db_path),
            patch.object(bot, "ADMIN_USER_IDS", {98765}),
        ):
            asyncio.run(bot.ai_control_command(update, SimpleNamespace(args=["off"])))
            self.assertEqual(bot.get_setting("ai_group:-42"), "off")
            chat.type = bot.ChatType.PRIVATE
            asyncio.run(bot.ai_control_command(update, SimpleNamespace(args=["off"])))
            self.assertEqual(bot.get_setting("ai_enabled"), "off")
            asyncio.run(
                bot.language_control_command(
                    update, SimpleNamespace(args=["Hindi", "off"])
                )
            )
            self.assertNotIn("Hindi", bot._enabled_languages())
            asyncio.run(
                bot.language_control_command(
                    update, SimpleNamespace(args=["Hindi", "on"])
                )
            )
            self.assertIn("Hindi", bot._enabled_languages())

    def test_admin_controls_reject_unauthorized_users(self):
        message = SimpleNamespace(reply_text=AsyncMock())
        update = SimpleNamespace(
            effective_message=message,
            effective_user=SimpleNamespace(id=123),
            effective_chat=SimpleNamespace(type=bot.ChatType.PRIVATE, id=123),
        )
        with patch.object(bot, "ADMIN_USER_IDS", {98765}):
            asyncio.run(bot.ai_control_command(update, SimpleNamespace(args=["off"])))
        message.reply_text.assert_awaited_once()
        self.assertIn("only to configured", message.reply_text.await_args.args[0])

    def test_ai_receives_relevant_guide_excerpt(self):
        guide = self.make_guide()
        async def response_stream():
            yield SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(content="Check the order details.")
                    )
                ]
            )

        create = AsyncMock(return_value=response_stream())
        client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=create),
            )
        )
        streamed_updates = []

        async def record_update(answer):
            streamed_updates.append(answer)

        with patch.object(bot, "DB_PATH", self.root / "history.sqlite3"):
            answer, pages = asyncio.run(
                bot._ai_answer(
                    "What should I check before paying?",
                    42,
                    guide,
                    client,
                    record_update,
                )
            )
        self.assertEqual(answer, "Check the order details.")
        self.assertEqual(streamed_updates, ["Check the order details."])
        self.assertTrue(pages)
        self.assertTrue(create.await_args.kwargs["stream"])
        self.assertEqual(create.await_args.kwargs["max_tokens"], 300)
        messages = create.await_args.kwargs["messages"]
        self.assertIn("recipient", messages[0]["content"])
        self.assertIn("same language", messages[0]["content"])
        self.assertIn("untrusted input", messages[0]["content"])

    def test_admin_faq_is_used_as_retrievable_answer_and_can_be_removed(self):
        db_path = self.root / "faqs.sqlite3"
        with patch.object(bot, "DB_PATH", db_path):
            bot.save_custom_faq(
                "wallet_ready",
                "Wallet readiness",
                ["wallet ready", "wallet active"],
                "Check the linked wallet status in the app.",
            )
            matches = bot._matching_custom_faqs("Is my wallet ready?")
            self.assertEqual(matches[0]["key"], "wallet_ready")
            answer, pages = asyncio.run(
                bot._ai_answer("Is my wallet ready?", 7, self.make_guide(), None)
            )
            self.assertIn("Check the linked wallet status", answer)
            self.assertIsInstance(pages, list)
            self.assertTrue(bot.remove_custom_faq("wallet_ready"))
            self.assertEqual(bot.list_custom_faqs(), [])

    def test_group_filter_skips_unrelated_unaddressed_conversation(self):
        self.assertFalse(self.make_guide().retrieve("what time is dinner", top_k=1))

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
        self.assertFalse(bot._contains_sensitive_input("How do I reset my password?"))
        self.assertFalse(bot._contains_sensitive_input("I forgot password reset"))
        self.assertTrue(bot._contains_sensitive_input("OTP code: 882133"))
        self.assertTrue(
            bot._contains_sensitive_input("bank account number is 1234567890123456")
        )

    def test_ai_does_not_use_unverified_training_notes_as_source(self):
        with patch.object(bot, "DB_PATH", self.root / "no-training.sqlite3"):
            answer, _ = asyncio.run(
                bot._ai_answer(
                    "My withdrawal is delayed",
                    42,
                    self.make_guide(),
                    None,
                )
            )
        self.assertIn("AI answers are not configured", answer)
        self.assertNotIn("Do not submit a duplicate withdrawal", answer)

    def test_ask_command_saves_private_follow_up_context(self):
        db_path = self.root / "ask-history.sqlite3"
        message = SimpleNamespace(reply_text=AsyncMock(), reply_photo=AsyncMock())
        update = SimpleNamespace(
            effective_message=message,
            effective_user=SimpleNamespace(id=77),
            effective_chat=SimpleNamespace(type=bot.ChatType.PRIVATE, id=77),
        )
        context = SimpleNamespace(
            args=["My", "withdrawal", "is", "pending"],
            application=SimpleNamespace(
                bot_data={"guide": self.make_guide(), "ai_client": object()}
            ),
        )
        with (
            patch.object(bot, "DB_PATH", db_path),
            patch.object(
                bot,
                "_ai_answer",
                new=AsyncMock(return_value=("Check your receiving limit.", [])),
            ) as ai_answer,
        ):
            asyncio.run(bot.ask_command(update, context))
            history = bot.get_history(77)
        ai_answer.assert_awaited_once()
        self.assertEqual(
            [entry["role"] for entry in history],
            ["user", "assistant"],
        )
        self.assertEqual(history[-1]["content"], "Check your receiving limit.")

    def test_report_user_text_is_sent_without_telegram_markdown_parsing(self):
        message = SimpleNamespace(
            text="/report 894235 *_[unexpected]_—",
            reply_text=AsyncMock(),
        )
        update = SimpleNamespace(
            effective_message=message,
            effective_user=SimpleNamespace(id=77, username="user_name", first_name="User"),
        )
        with patch.object(bot, "DB_PATH", self.root / "report.sqlite3"):
            asyncio.run(bot.report_command(update, SimpleNamespace()))
        message.reply_text.assert_awaited_once()
        sent_text = message.reply_text.await_args.args[0]
        self.assertIn("*_[unexpected]_—", sent_text)
        self.assertNotIn("parse_mode", message.reply_text.await_args.kwargs)

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

    def test_group_messages_only_trigger_when_mentioning_or_replying_to_bot(self):
        bot_user = SimpleNamespace(id=777, username="ipaybot")
        mention = SimpleNamespace(type="mention", extract=lambda text: "@ipaybot")
        mentioned_message = SimpleNamespace(
            text="@ipaybot where is my deposit?",
            entities=[mention],
        )
        unrelated_message = SimpleNamespace(
            text="@someone where is my deposit?",
            entities=[SimpleNamespace(type="mention", extract=lambda text: "@someone")],
        )
        reply_message = SimpleNamespace(
            text="My withdrawal is delayed",
            reply_to_message=SimpleNamespace(
                from_user=SimpleNamespace(id=777),
            ),
        )

        self.assertTrue(bot._is_group_message_for_bot(mentioned_message, bot_user))
        self.assertFalse(bot._is_group_message_for_bot(unrelated_message, bot_user))
        self.assertTrue(bot._is_group_message_for_bot(reply_message, bot_user))

    def test_mentioned_group_question_gets_an_isolated_reply(self):
        message = SimpleNamespace(
            text="@ipaybot My deposit is delayed",
            entities=[
                SimpleNamespace(type="mention", extract=lambda text: "@ipaybot")
            ],
            message_id=501,
            reply_text=AsyncMock(),
        )
        user = SimpleNamespace(id=123)
        update = SimpleNamespace(
            effective_message=message,
            effective_user=user,
            effective_chat=SimpleNamespace(type=bot.ChatType.SUPERGROUP, id=-10042),
        )
        context = SimpleNamespace(
            bot=SimpleNamespace(id=777, username="ipaybot"),
            application=SimpleNamespace(
                bot_data={
                    "guide": self.make_guide(),
                    "ai_client": object(),
                    "last_message_at": {},
                }
            ),
        )
        conversation_id = bot._group_conversation_id(-10042, 123, 501)
        with (
            patch.object(
                bot,
                "_ai_answer",
                new=AsyncMock(return_value=("Check the payment status.", [])),
            ) as ai_answer,
            patch.object(bot, "add_message") as add_message,
        ):
            asyncio.run(bot.ai_reply(update, context))

        ai_answer.assert_awaited_once()
        self.assertEqual(ai_answer.await_args.args[1], conversation_id)
        self.assertNotEqual(conversation_id, -10042)
        self.assertEqual(add_message.call_count, 2)
        self.assertEqual(add_message.call_args_list[0].args[0], conversation_id)
        self.assertIn("Check the payment status.", message.reply_text.await_args.args[0])

    def test_unaddressed_ipay_group_question_gets_a_reply(self):
        message = SimpleNamespace(
            text="My deposit is delayed",
            message_id=502,
            reply_text=AsyncMock(),
        )
        update = SimpleNamespace(
            effective_message=message,
            effective_user=SimpleNamespace(id=123),
            effective_chat=SimpleNamespace(type=bot.ChatType.GROUP, id=-42),
        )
        context = SimpleNamespace(
            bot=SimpleNamespace(id=777, username="ipaybot"),
            application=SimpleNamespace(
                bot_data={
                    "guide": self.make_guide(),
                    "ai_client": object(),
                    "last_message_at": {},
                }
            ),
        )
        with (
            patch.object(bot, "DB_PATH", self.root / "group-settings.sqlite3"),
            patch.object(
                bot,
                "_ai_answer",
                new=AsyncMock(return_value=("Check the order status.", [])),
            ) as ai_answer,
        ):
            asyncio.run(bot.ai_reply(update, context))
        ai_answer.assert_awaited_once()
        self.assertIn("Check the order status.", message.reply_text.await_args.args[0])

    def test_group_greeting_to_bot_only_sends_short_greeting(self):
        message = SimpleNamespace(
            text="@ipaybot hi",
            entities=[
                SimpleNamespace(type="mention", extract=lambda text: "@ipaybot")
            ],
            message_id=503,
            reply_text=AsyncMock(),
            reply_photo=AsyncMock(),
        )
        update = SimpleNamespace(
            effective_message=message,
            effective_user=SimpleNamespace(id=123),
            effective_chat=SimpleNamespace(type=bot.ChatType.SUPERGROUP, id=-10042),
        )
        context = SimpleNamespace(
            bot=SimpleNamespace(id=777, username="ipaybot"),
            application=SimpleNamespace(
                bot_data={"guide": self.make_guide(), "last_message_at": {}}
            ),
        )
        with patch.object(bot, "_send_home_message", new=AsyncMock()) as home_message:
            asyncio.run(bot.ai_reply(update, context))

        message.reply_text.assert_awaited_once_with(
            "Please tell me what you need help with."
        )
        message.reply_photo.assert_not_awaited()
        home_message.assert_not_awaited()

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

    def test_withdrawal_image_and_explanation_replace_current_menu(self):
        custom_images = self.root / "custom_images"
        custom_images.mkdir()
        (custom_images / "withdrawal_delay.jpg").write_bytes(b"test image")
        query = SimpleNamespace(
            data="issue:withdrawal",
            message=SimpleNamespace(
                photo=[object()],
                reply_text=AsyncMock(),
                reply_photo=AsyncMock(),
            ),
            answer=AsyncMock(),
            delete_message=AsyncMock(),
            edit_message_media=AsyncMock(),
            edit_message_text=AsyncMock(),
            edit_message_caption=AsyncMock(),
        )
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={"guide": self.make_guide()})
        )
        with patch.object(bot, "CUSTOM_IMAGES_DIR", custom_images):
            asyncio.run(bot.handle_callback(SimpleNamespace(callback_query=query), context))
        query.edit_message_media.assert_not_awaited()
        query.message.reply_photo.assert_awaited_once()
        query.delete_message.assert_awaited_once()
        query.message.reply_text.assert_not_awaited()
        caption = query.message.reply_photo.await_args.kwargs["caption"]
        self.assertEqual(caption, bot._issue_answer("withdrawal")[0])
        self.assertIn("Check your daily UPI receiving limit.", caption)
        self.assertIn("Try relinking your wallet if needed.", caption)
        self.assertIn("Wait for the status update.", caption)
        self.assertIn("Contact iPay support if the delay continues.", caption)
        self.assertNotIn("guide", caption.lower())
        self.assertNotIn("Owner-provided", caption)

    def test_issue_image_replaces_home_image_without_posting_second_photo(self):
        custom_images = self.root / "custom_images"
        custom_images.mkdir()
        (custom_images / "withdrawal_delay.jpg").write_bytes(b"test image")
        message = SimpleNamespace(
            photo=[object()],
            reply_text=AsyncMock(),
            reply_photo=AsyncMock(),
        )
        query = SimpleNamespace(
            data="issue:withdrawal",
            message=message,
            answer=AsyncMock(),
            delete_message=AsyncMock(),
            edit_message_media=AsyncMock(),
            edit_message_caption=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={"guide": self.make_guide()})
        )
        with patch.object(bot, "CUSTOM_IMAGES_DIR", custom_images):
            asyncio.run(
                bot.handle_callback(SimpleNamespace(callback_query=query), context)
            )
        query.edit_message_media.assert_not_awaited()
        message.reply_photo.assert_awaited_once()
        query.delete_message.assert_awaited_once()
        self.assertEqual(
            message.reply_photo.await_args.kwargs["photo"].filename,
            "withdrawal_delay.jpg",
        )

    def test_issue_image_replaces_current_message_without_editing(self):
        custom_images = self.root / "custom_images"
        custom_images.mkdir()
        (custom_images / "deposit_problem.jpg").write_bytes(b"test image")
        message = SimpleNamespace(
            photo=[object()],
            reply_text=AsyncMock(),
            reply_photo=AsyncMock(),
        )
        query = SimpleNamespace(
            data="issue:deposit",
            message=message,
            answer=AsyncMock(),
            delete_message=AsyncMock(),
            edit_message_media=AsyncMock(),
            edit_message_caption=AsyncMock(),
            edit_message_text=AsyncMock(),
            edit_message_reply_markup=AsyncMock(),
        )
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={"guide": self.make_guide()})
        )
        with patch.object(bot, "CUSTOM_IMAGES_DIR", custom_images):
            asyncio.run(
                bot.handle_callback(SimpleNamespace(callback_query=query), context)
            )

        query.edit_message_media.assert_not_awaited()
        message.reply_photo.assert_awaited_once()
        message.reply_text.assert_not_awaited()
        query.edit_message_caption.assert_not_awaited()
        query.delete_message.assert_awaited_once()
        self.assertEqual(
            message.reply_photo.await_args.kwargs["photo"].filename,
            "deposit_problem.jpg",
        )
        self.assertIn(
            "◆ iPAY – DEPOSIT ISSUE",
            message.reply_photo.await_args.kwargs["caption"],
        )

    def test_stale_cached_image_id_retries_upload_with_local_file(self):
        image_path = self.root / "deposit_problem.jpg"
        image_path.write_bytes(b"test image")
        bot.PHOTO_FILE_IDS[bot._image_cache_key(image_path)] = "stale-file-id"
        message = SimpleNamespace(
            photo=[object()],
            reply_photo=AsyncMock(
                side_effect=[
                    BadRequest("Photo file not found"),
                    SimpleNamespace(photo=[SimpleNamespace(file_id="fresh-file-id")]),
                ]
            ),
        )
        query = SimpleNamespace(
            message=message,
            delete_message=AsyncMock(),
        )
        result = asyncio.run(
            bot._replace_callback_image(
                SimpleNamespace(callback_query=query),
                image_path,
                "Deposit help",
                bot.issues_keyboard(),
            )
        )
        self.assertTrue(result)
        self.assertEqual(message.reply_photo.await_count, 2)
        retried_photo = message.reply_photo.await_args.kwargs["photo"]
        self.assertIsInstance(retried_photo, bot.InputFile)
        query.delete_message.assert_awaited_once()
        self.assertEqual(
            bot.PHOTO_FILE_IDS[bot._image_cache_key(image_path)],
            "fresh-file-id",
        )

    def test_back_from_issue_details_returns_to_issue_menu(self):
        image_dir = Path(__file__).resolve().parents[1] / "custom_images"
        message = SimpleNamespace(
            photo=[object()],
            chat=SimpleNamespace(id=54),
            message_id=900,
            reply_text=AsyncMock(),
            reply_photo=AsyncMock(),
        )
        query = SimpleNamespace(
            data="menu:issues",
            message=message,
            from_user=SimpleNamespace(id=54),
            answer=AsyncMock(),
            delete_message=AsyncMock(),
            edit_message_media=AsyncMock(),
            edit_message_caption=AsyncMock(),
            edit_message_text=AsyncMock(),
            edit_message_reply_markup=AsyncMock(),
        )
        update = SimpleNamespace(
            callback_query=query,
            effective_chat=message.chat,
            effective_user=query.from_user,
        )
        context = SimpleNamespace(
            application=SimpleNamespace(
                bot_data={"guide": self.make_guide(), "menu_history": {}}
            )
        )
        with (
            patch.object(bot, "ISSUES_IMAGE_PATH", image_dir / "customer_service.png"),
            patch.object(bot, "CUSTOM_IMAGES_DIR", image_dir),
        ):
            asyncio.run(bot.handle_callback(update, context))

            query.data = "issue:deposit"
            asyncio.run(bot.handle_callback(update, context))

            query.data = "nav:back"
            asyncio.run(bot.handle_callback(update, context))

        query.edit_message_media.assert_not_awaited()
        self.assertEqual(message.reply_photo.await_count, 3)
        final_menu = message.reply_photo.await_args.kwargs
        self.assertEqual(final_menu["photo"].filename, "customer_service.png")
        self.assertIn(
            "Identify your issue and click the button below",
            final_menu["caption"],
        )
        history_key = (message.chat.id, query.from_user.id)
        self.assertEqual(
            context.application.bot_data["menu_history"][history_key],
            ["nav:menu", "menu:issues"],
        )

    def test_deposit_issue_replaces_menu_image_with_supplied_image(self):
        custom_images = self.root / "custom_images"
        custom_images.mkdir()
        (custom_images / "deposit_problem.jpg").write_bytes(b"test image")
        message = SimpleNamespace(
            photo=[object()],
            reply_text=AsyncMock(),
            reply_photo=AsyncMock(),
        )
        query = SimpleNamespace(
            data="issue:deposit",
            message=message,
            answer=AsyncMock(),
            delete_message=AsyncMock(),
            edit_message_media=AsyncMock(),
            edit_message_caption=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={"guide": self.make_guide()})
        )
        with patch.object(bot, "CUSTOM_IMAGES_DIR", custom_images):
            asyncio.run(
                bot.handle_callback(SimpleNamespace(callback_query=query), context)
            )
        query.edit_message_media.assert_not_awaited()
        query.delete_message.assert_awaited_once()
        self.assertEqual(message.reply_photo.await_count, 1)
        media = message.reply_photo.await_args.kwargs
        self.assertEqual(media["photo"].filename, "deposit_problem.jpg")
        self.assertNotIn("Owner-provided deposit illustration", media["caption"])
        self.assertNotIn("24-hour timing", media["caption"])
        self.assertEqual(media["caption"], bot._issue_answer("deposit")[0])
        self.assertTrue(media["caption"].startswith("◆ iPAY – DEPOSIT ISSUE"))
        self.assertIn("Use your iPay-linked wallet only.", media["caption"])
        self.assertIn("Payment pending? Check the status before paying again.", media["caption"])
        self.assertNotIn("Owner-provided", media["caption"])
        self.assertEqual(message.reply_text.await_count, 0)

    def test_upi_issue_uses_supplied_image(self):
        custom_images = self.root / "custom_images"
        custom_images.mkdir()
        (custom_images / "upi_locked.jpg").write_bytes(b"test image")
        message = SimpleNamespace(
            photo=[object()],
            reply_text=AsyncMock(),
            reply_photo=AsyncMock(),
        )
        query = SimpleNamespace(
            data="issue:upi",
            message=message,
            answer=AsyncMock(),
            delete_message=AsyncMock(),
            edit_message_media=AsyncMock(),
            edit_message_caption=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={"guide": self.make_guide()})
        )
        with patch.object(bot, "CUSTOM_IMAGES_DIR", custom_images):
            asyncio.run(
                bot.handle_callback(SimpleNamespace(callback_query=query), context)
            )
        query.edit_message_media.assert_not_awaited()
        message.reply_photo.assert_awaited_once()
        query.delete_message.assert_awaited_once()
        self.assertEqual(
            message.reply_photo.await_args.kwargs["photo"].filename,
            "upi_locked.jpg",
        )
        caption = message.reply_photo.await_args.kwargs["caption"]
        self.assertEqual(
            caption,
            "◆ iPAY – UPI BLOCKED / LOCKED\n\n"
            "🔍 IMPORTANT CHECKS\n"
            "📱 Follow the instructions shown in the above image.\n"
            "🏦 Check for restrictions from your bank or wallet provider.\n\n"
            "⚙️ WHAT TO DO NEXT\n"
            "🚫 Avoid repeated payment attempts while UPI is locked.\n"
            "💸 Make a ₹1 transfer to your own UPI only if instructed by the app.\n"
            "🏦 Contact your bank or wallet provider if UPI is blocked.\n"
            "📞 Contact iPay support if the issue continues.",
        )
        self.assertNotIn("Owner-provided", caption)

    def test_order_locked_issue_uses_requested_guidance(self):
        custom_images = self.root / "custom_images"
        custom_images.mkdir()
        (custom_images / "order_locked.jpg").write_bytes(b"test image")
        message = SimpleNamespace(
            photo=[object()],
            reply_text=AsyncMock(),
            reply_photo=AsyncMock(),
        )
        query = SimpleNamespace(
            data="issue:locked",
            message=message,
            answer=AsyncMock(),
            delete_message=AsyncMock(),
        )
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={"guide": self.make_guide()})
        )
        with patch.object(bot, "CUSTOM_IMAGES_DIR", custom_images):
            asyncio.run(
                bot.handle_callback(SimpleNamespace(callback_query=query), context)
            )
        message.reply_photo.assert_awaited_once()
        query.delete_message.assert_awaited_once()
        caption = message.reply_photo.await_args.kwargs["caption"]
        self.assertEqual(
            caption,
            "◆ iPAY – ORDER LOCKED\n\n"
            "🔍 WHAT TO DO NEXT\n\n"
            "📱 Check your wallet status and follow the iPay app instructions.\n"
            "🔄 Relink your wallet and wait for the system to process.\n"
            "🚫 Avoid third-party logins and duplicate payments.\n"
            "⏳ The system updates every 10 minutes. Keep your wallet ready and wait "
            "for processing.\n"
            "📞 Contact iPay support if the issue continues.\n\n"
            "◆ RELINK • WAIT • STAY UPDATED!",
        )

    def test_commission_issue_uses_image_with_structured_guidance(self):
        custom_images = self.root / "custom_images"
        custom_images.mkdir()
        (custom_images / "commission.jpg").write_bytes(b"test image")
        message = SimpleNamespace(
            photo=[object()],
            reply_text=AsyncMock(),
            reply_photo=AsyncMock(),
        )
        query = SimpleNamespace(
            data="issue:commission",
            message=message,
            answer=AsyncMock(),
            delete_message=AsyncMock(),
            edit_message_media=AsyncMock(),
            edit_message_caption=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={"guide": self.make_guide()})
        )
        with patch.object(bot, "CUSTOM_IMAGES_DIR", custom_images):
            asyncio.run(
                bot.handle_callback(SimpleNamespace(callback_query=query), context)
            )
        query.edit_message_media.assert_not_awaited()
        query.delete_message.assert_awaited_once()
        media = message.reply_photo.await_args.kwargs
        self.assertEqual(media["photo"].filename, "commission.jpg")
        self.assertTrue(media["caption"].startswith("COMMISSION\n\nKEY POINTS"))
        self.assertIn("Rates and bonuses can vary by offer and completed order.", media["caption"])
        self.assertNotIn("WHAT TO DO NEXT", media["caption"])
        self.assertNotIn("Ask support to confirm the rate", media["caption"])
        self.assertNotIn("guide", media["caption"].lower())
        self.assertNotIn("Owner-provided", media["caption"])
        self.assertEqual(message.reply_photo.await_count, 1)
        self.assertEqual(message.reply_text.await_count, 0)

    def test_rewards_issue_sends_quota_illustration_with_structured_guidance(self):
        custom_images = self.root / "custom_images"
        custom_images.mkdir()
        (custom_images / "reward_quota.jpg").write_bytes(b"test image")
        query = SimpleNamespace(
            data="issue:rewards",
            message=SimpleNamespace(
                photo=[object()],
                reply_text=AsyncMock(),
                reply_photo=AsyncMock(),
            ),
            answer=AsyncMock(),
            delete_message=AsyncMock(),
            edit_message_text=AsyncMock(),
            edit_message_media=AsyncMock(),
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
        query.edit_message_media.assert_not_awaited()
        query.delete_message.assert_awaited_once()
        query.message.reply_photo.assert_awaited_once()
        caption = query.message.reply_photo.await_args.kwargs["caption"]
        self.assertTrue(caption.startswith("MISSING REWARD\n\nCHECK THESE KEY POINTS"))
        self.assertIn("Ask support to clarify any task threshold or calculation.", caption)
        self.assertNotIn("guide", caption.lower())
        self.assertNotIn("Owner-provided", caption)

    def test_wallet_menu_replaces_current_message_without_editing(self):
        query = SimpleNamespace(
            data="menu:wallets",
            message=SimpleNamespace(
                photo=[object()],
                reply_photo=AsyncMock(),
                reply_text=AsyncMock(),
            ),
            answer=AsyncMock(),
            delete_message=AsyncMock(),
            edit_message_text=AsyncMock(),
            edit_message_caption=AsyncMock(),
            edit_message_media=AsyncMock(),
            edit_message_reply_markup=AsyncMock(),
        )
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={"guide": self.make_guide()})
        )
        with patch.object(
            bot,
            "WALLET_IMAGE_PATH",
            Path(__file__).resolve().parents[1]
            / "custom_images"
            / "supported_wallets.png",
        ):
            asyncio.run(
                bot.handle_callback(
                    SimpleNamespace(callback_query=query),
                    context,
                )
            )
        query.edit_message_media.assert_not_awaited()
        query.message.reply_photo.assert_awaited_once()
        query.delete_message.assert_awaited_once()
        sent_photo = query.message.reply_photo.await_args.kwargs
        self.assertEqual(sent_photo["photo"].filename, "supported_wallets.png")
        self.assertEqual(
            sent_photo["caption"],
            (
                "◆ iPAY – SUPPORTED WALLETS\n\n"
                "📈 PAYIN WALLETS\n"
                "▪️ ✅ PhonePe\n"
                "▪️ ✅ Paytm\n"
                "▪️ ✅ MobiKwik\n"
                "▪️ ✅ Freecharge\n\n"
                "💼 BUSINESS WALLETS\n"
                "▪️ Google Pay Business\n"
                "▪️ PhonePe Business\n"
                "▪️ Paytm Business\n\n"
                "⚠️ Business wallets are recommended for smoother collections."
            ),
        )
        markup = sent_photo["reply_markup"]
        self.assertEqual(
            [button.callback_data for row in markup.inline_keyboard for button in row],
            [
                "wallet:payin",
                "wallet:payout",
                "nav:back",
            ],
        )

    def test_wallet_command_uses_the_supported_wallet_image(self):
        message = SimpleNamespace(
            reply_photo=AsyncMock(),
            reply_text=AsyncMock(),
        )
        asyncio.run(bot._send_wallets(message))
        message.reply_photo.assert_awaited_once()
        sent_photo = message.reply_photo.await_args.kwargs
        self.assertEqual(sent_photo["photo"].filename, "supported_wallets.png")
        self.assertIn("SUPPORTED WALLETS", sent_photo["caption"])

    def test_payin_and_payout_update_caption_without_replacing_wallet_image(self):
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={"guide": self.make_guide()})
        )
        for action, expected_title, expected_wallets in (
            ("wallet:payin", "PAYIN", ("PhonePe", "Paytm", "MobiKwik", "Freecharge")),
            (
                "wallet:payout",
                "PAYOUT",
                ("PhonePe", "Paytm", "MobiKwik", "Freecharge"),
            ),
        ):
            with self.subTest(action=action), patch.object(
                bot,
                "WALLET_IMAGE_PATH",
                Path(__file__).resolve().parents[1]
                / "custom_images"
                / "supported_wallets.png",
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
                    delete_message=AsyncMock(),
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
                query.edit_message_media.assert_not_awaited()
                query.message.reply_photo.assert_awaited_once()
                query.delete_message.assert_awaited_once()
                media = query.message.reply_photo.await_args.kwargs
                self.assertEqual(media["photo"].filename, "supported_wallets.png")
                self.assertIn(expected_title, media["caption"])
                for wallet in expected_wallets:
                    self.assertIn(wallet, media["caption"])
                if action == "wallet:payout":
                    self.assertNotIn("Google Pay", media["caption"])
                    self.assertNotIn("Business", media["caption"])
                    self.assertEqual(
                        media["caption"],
                        (
                            "◆ iPAY – SUPPORTED WALLETS\n\n"
                            "💸 PAYOUT WALLETS\n"
                            "▪️ PhonePe\n"
                            "▪️ Paytm\n"
                            "▪️ MobiKwik\n"
                            "▪️ Freecharge\n\n"
                            "◆ USE SUPPORTED WALLETS FOR SMOOTH PAYOUTS!"
                        ),
                    )
                else:
                    self.assertIn("Google Pay Business", media["caption"])
                    self.assertIn("PhonePe Business", media["caption"])
                    self.assertIn("Paytm Business", media["caption"])
                    self.assertIn(
                        "Business wallets are recommended for smoother collections.",
                        media["caption"],
                    )
                markup = media["reply_markup"]
                self.assertEqual(
                    [button.callback_data for row in markup.inline_keyboard for button in row],
                    [
                        "wallet:payin",
                        "wallet:payout",
                        "nav:back",
                    ],
                )

    def test_boost_command_requires_six_digit_uid(self):
        message = SimpleNamespace(text="/boost 79098", reply_text=AsyncMock())
        update = SimpleNamespace(effective_message=message)
        context = SimpleNamespace(args=["79098"])
        asyncio.run(bot.boost_command(update, context))
        message.reply_text.assert_awaited_once()
        self.assertIn("six-digit iPAY UID", message.reply_text.await_args.args[0])
        self.assertNotIn("invalid", message.reply_text.await_args.args[0].lower())

    def test_boost_command_limits_each_user_to_one_request_per_five_minutes(self):
        db_path = self.root / "boost-cooldowns.sqlite3"
        message = SimpleNamespace(text="/boost 749488", reply_text=AsyncMock())
        user = SimpleNamespace(id=333, username="test_user", first_name="Test")
        update = SimpleNamespace(effective_message=message, effective_user=user)
        context = SimpleNamespace(args=["749488"], user_data={})
        with patch.object(bot, "DB_PATH", db_path), \
                patch.object(bot.time, "time", side_effect=[1000, 1100]), \
                patch.object(bot, "save_user_uid"), \
                patch.object(bot.random, "choice", side_effect=lambda styles: styles[0]):
            asyncio.run(bot.boost_command(update, context))
            asyncio.run(bot.boost_command(update, context))
        responses = [call.args[0] for call in message.reply_text.await_args_list]
        self.assertEqual(len(responses), 2)
        self.assertIn("Your UID was received", responses[0])
        self.assertIn("Please wait 3m 20s", responses[1])
        with patch.object(bot, "DB_PATH", db_path):
            conn = bot.db()
            try:
                requests = conn.execute(
                    "SELECT outcome, remaining_seconds FROM boost_requests "
                    "WHERE user_id=? ORDER BY requested_at",
                    (333,),
                ).fetchall()
            finally:
                conn.close()
        self.assertEqual(requests, [("received", 0), ("rate_limited", 200)])

    def test_boost_caption_routes_uid_and_request_text(self):
        db_path = self.root / "boost-caption.sqlite3"
        message = SimpleNamespace(
            caption="218532 boost my withdrawal",
            reply_text=AsyncMock(),
        )
        user = SimpleNamespace(id=444, username="trader", first_name="Trader")
        update = SimpleNamespace(effective_message=message, effective_user=user)
        context = SimpleNamespace(args=[], user_data={})
        with patch.object(bot, "DB_PATH", db_path), \
                patch.object(bot, "save_user_uid"), \
                patch.object(bot.random, "choice", side_effect=lambda styles: styles[0]):
            asyncio.run(bot.boost_caption_handler(update, context))
        message.reply_text.assert_awaited_once()
        response = message.reply_text.await_args.args[0]
        self.assertIn("UID:</b> 218532", response)
        self.assertIn("WITHDRAWAL SUPPORT DETAILS", response)
        with patch.object(bot, "DB_PATH", db_path):
            conn = bot.db()
            try:
                request = conn.execute(
                    "SELECT uid, source, outcome FROM boost_requests WHERE user_id=?",
                    (444,),
                ).fetchone()
            finally:
                conn.close()
        self.assertEqual(request, ("218532", "caption", "received"))

    def test_boost_command_has_no_inline_buttons(self):
        message = SimpleNamespace(text="/boost 749488", reply_text=AsyncMock())
        user = SimpleNamespace(id=333, username="test_user", first_name="Test")
        update = SimpleNamespace(effective_message=message, effective_user=user)
        context = SimpleNamespace(args=["749488"])
        with patch.object(bot, "save_user_uid") as save_user_uid, \
                patch.object(bot, "reserve_boost_request", return_value=0), \
                patch.object(bot, "record_boost_request"), \
                patch.object(bot.random, "choice", side_effect=lambda styles: styles[0]):
            asyncio.run(bot.boost_command(update, context))
        save_user_uid.assert_called_once_with(333, "test_user", "Test", "749488")
        response = message.reply_text.await_args.args[0]
        self.assertIn("WITHDRAWAL SUPPORT DETAILS", response)
        self.assertIn("User:</b> @test_user", response)
        self.assertIn("UID:</b> 749488", response)
        self.assertIn("Your UID was received for the withdrawal boost", response)
        self.assertNotIn("cannot activate a withdrawal boost", response)
        self.assertNotIn("Contact support:", response)
        self.assertNotIn("Priority Support Activated", response)
        self.assertTrue(response.endswith("◆ iPAY – MOVE WITH PURPOSE!"))
        self.assertNotIn("reply_markup", message.reply_text.await_args.kwargs)

    def test_uid_and_notice_commands_tag_user_and_show_notice(self):
        db_path = self.root / "test.db"
        with patch.object(bot, "DB_PATH", str(db_path)):
            message = SimpleNamespace(reply_text=AsyncMock(), text="/uid 894235")
            user = SimpleNamespace(id=5555, username="rajesh", first_name="Rajesh")
            update = SimpleNamespace(effective_message=message, effective_user=user)
            context = SimpleNamespace(args=["894235"])
            asyncio.run(bot.uid_command(update, context))
            message.reply_text.assert_awaited_once()
            sent_text = message.reply_text.await_args.args[0]
            self.assertIn("@rajesh", sent_text)
            self.assertIn("894235", sent_text)
            self.assertIn("USER SUPPORT NOTICE", sent_text)
            self.assertIn("cannot verify", sent_text)

            # Check lookup via tag command
            tag_message = SimpleNamespace(reply_text=AsyncMock(), text="/tag 894235")
            tag_caller = SimpleNamespace(id=9999, username="admin", first_name="Admin")
            tag_update = SimpleNamespace(effective_message=tag_message, effective_user=tag_caller)
            tag_context = SimpleNamespace(args=["894235"])
            asyncio.run(bot.tag_command(tag_update, tag_context))
            tag_message.reply_text.assert_awaited_once()
            tag_text = tag_message.reply_text.await_args.args[0]
            self.assertIn("@rajesh", tag_text)
            self.assertIn("894235", tag_text)

            # Check notice command
            notice_message = SimpleNamespace(reply_text=AsyncMock(), text="/notice 894235")
            notice_update = SimpleNamespace(effective_message=notice_message, effective_user=tag_caller)
            notice_context = SimpleNamespace(args=["894235"])
            asyncio.run(bot.notice_command(notice_update, notice_context))
            notice_message.reply_text.assert_awaited_once()
            notice_text = notice_message.reply_text.await_args.args[0]
            self.assertIn("@rajesh", notice_text)
            self.assertIn("894235", notice_text)
            self.assertIn("USER SUPPORT NOTICE", notice_text)

    def test_boost_command_uses_acknowledgment_without_support_footer(self):
        message = SimpleNamespace(text="/boost 749488", reply_text=AsyncMock())
        user = SimpleNamespace(id=333, username="test_user", first_name="Test")
        update = SimpleNamespace(effective_message=message, effective_user=user)
        context = SimpleNamespace(args=["749488"])
        with patch.object(bot, "save_user_uid"), \
                patch.object(bot, "reserve_boost_request", return_value=0), \
                patch.object(bot, "record_boost_request"), \
                patch.object(bot.random, "choice", side_effect=lambda styles: styles[0]):
            asyncio.run(bot.boost_command(update, context))
        response = message.reply_text.await_args.args[0]
        self.assertIn("WITHDRAWAL SUPPORT DETAILS", response)
        self.assertIn("Your UID was received for the withdrawal boost", response)
        self.assertNotIn("Contact support:", response)
        self.assertNotIn("cannot activate a withdrawal boost", response)
        self.assertNotIn("ACTIVATED SUCCESSFULLY", response)
        self.assertNotIn("Priority Support Activated", response)

    def test_boost_reply_style_is_selected_randomly_for_each_request(self):
        context = SimpleNamespace(args=["749488"], user_data={})
        user = SimpleNamespace(id=333, username="test_user", first_name="Test")
        acknowledgments = []
        captions = []
        selections = iter(range(7))

        def choose_style(styles):
            return styles[next(selections)]

        with patch.object(bot, "reserve_boost_request", return_value=0), \
                patch.object(bot, "record_boost_request"), \
                patch.object(bot, "save_user_uid"), \
                patch.object(bot.random, "choice", side_effect=choose_style) as choose:
            for _ in range(7):
                message = SimpleNamespace(text="/boost 749488", reply_text=AsyncMock())
                update = SimpleNamespace(effective_message=message, effective_user=user)
                asyncio.run(bot.boost_command(update, context))
                response = message.reply_text.await_args.args[0]
                self.assertNotIn("cannot activate a withdrawal boost", response)
                self.assertNotIn("Contact support:", response)
                self.assertNotIn("Priority Support Activated", response)
                self.assertNotIn("ACTIVATED SUCCESSFULLY", response)
                acknowledgments.append(
                    next(line for line in response.splitlines() if "withdrawal boost" in line.lower())
                )
                captions.append(response.splitlines()[-1])
        self.assertEqual(choose.call_count, 7)
        self.assertEqual(context.user_data, {})
        self.assertEqual(len(set(acknowledgments)), 7)
        self.assertEqual(len(set(captions)), 7)

    def test_ai_reply_triggers_boost_on_typos(self):
        message = SimpleNamespace(text="bost 789983", reply_text=AsyncMock(), message_id=123)
        chat = SimpleNamespace(id=100, type="private")
        user = SimpleNamespace(id=444, username="trader", first_name="Trader")
        update = SimpleNamespace(effective_message=message, effective_chat=chat, effective_user=user)
        app = SimpleNamespace(bot_data={"guide": MagicMock()})
        context = SimpleNamespace(application=app)
        with patch.object(bot, "save_user_uid"), \
                patch.object(bot, "reserve_boost_request", return_value=0), \
                patch.object(bot, "record_boost_request"), \
                patch.object(bot.random, "choice", side_effect=lambda styles: styles[0]):
            asyncio.run(bot.ai_reply(update, context))
        message.reply_text.assert_awaited_once()
        response = message.reply_text.await_args.args[0]
        self.assertIn("789983", response)
        self.assertIn("WITHDRAWAL SUPPORT DETAILS", response)
        self.assertIn("Your UID was received for the withdrawal boost", response)
        self.assertNotIn("Contact support:", response)

    def test_ai_reply_prompts_for_uid_instead_of_using_general_ai_for_boost(self):
        message = SimpleNamespace(text="please boost my withdrawal", reply_text=AsyncMock(), message_id=124)
        chat = SimpleNamespace(id=100, type="private")
        user = SimpleNamespace(id=445, username="trader", first_name="Trader")
        update = SimpleNamespace(effective_message=message, effective_chat=chat, effective_user=user)
        app = SimpleNamespace(bot_data={"guide": MagicMock()})
        context = SimpleNamespace(application=app)

        asyncio.run(bot.ai_reply(update, context))

        message.reply_text.assert_awaited_once_with(bot.BOOST_UID_PROMPT)
        self.assertNotIn("cannot", message.reply_text.await_args.args[0].lower())
        self.assertNotIn("contact support", message.reply_text.await_args.args[0].lower())

    def test_new_user_center_buttons_deliver_expected_content(self):
        guide = self.make_guide()
        pdf_path = self.root / "I_PAY_User_Guide.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\ntest pdf")
        how_works = self.root / "how_ipay_works.jpg"
        place_order = self.root / "how_to_place_order.jpg"
        referral = self.root / "referral_rewards.png"
        guidelines = self.root / "smart_guidelines.jpg"
        suggestion = self.root / "security_scammer_reminder.jpg"
        for img in (how_works, place_order, referral, guidelines, suggestion):
            img.write_bytes(b"img")

        context = SimpleNamespace(application=SimpleNamespace(bot_data={"guide": guide}))

        def make_update(action: str):
            msg = SimpleNamespace(
                chat_id=100,
                photo=[],
                reply_text=AsyncMock(),
                reply_photo=AsyncMock(),
                reply_document=AsyncMock(),
            )
            q = SimpleNamespace(
                data=action,
                message=msg,
                answer=AsyncMock(),
                delete_message=AsyncMock(),
            )
            return SimpleNamespace(callback_query=q, effective_chat=SimpleNamespace(id=100), effective_user=SimpleNamespace(id=1)), msg

        with (
            patch.object(bot, "GUIDE_PDF_PATH", pdf_path),
            patch.object(bot, "HOW_IPAY_WORKS_IMAGE_PATH", how_works),
            patch.object(bot, "HOW_TO_PLACE_ORDER_IMAGE_PATH", place_order),
            patch.object(bot, "REFERRAL_IMAGE_PATH", referral),
            patch.object(bot, "GUIDELINES_IMAGE_PATH", guidelines),
            patch.object(bot, "SUGGESTION_IMAGE_PATH", suggestion),
        ):
            update, msg = make_update("menu:new_user")
            asyncio.run(bot.handle_callback(update, context))
            msg.reply_photo.assert_awaited_once()
            self.assertNotIn("caption", msg.reply_photo.await_args.kwargs)
            self.assertTrue(
                all(len(row) == 1 for row in bot.new_user_keyboard().inline_keyboard)
            )

            update, msg = make_update("newuser:guide")
            asyncio.run(bot.handle_callback(update, context))
            self.assertEqual(msg.reply_document.await_args.kwargs["filename"], "I_PAY_User_Guide.pdf")

            for action, expected in (
                ("newuser:how_works", "Register your iPAY account."),
                ("newuser:place_order", "Step 9: Wait for your order confirmation."),
                ("newuser:referral", "Complete 15 orders on the same day."),
                ("newuser:security_alerts", "Never share your OTP, PIN, or password."),
                ("newuser:sales_settings", "Turn OFF sales when not required."),
            ):
                update, msg = make_update(action)
                asyncio.run(bot.handle_callback(update, context))
                msg.reply_photo.assert_awaited_once()
                self.assertIn(expected, msg.reply_photo.await_args.kwargs["caption"])

            update, msg = make_update("newuser:suggestion")
            asyncio.run(bot.handle_callback(update, context))
            msg.reply_photo.assert_awaited_once()
            self.assertIn("SMART SALES SETTINGS", msg.reply_photo.await_args.kwargs["caption"])
            self.assertEqual(msg.reply_photo.await_args.kwargs["parse_mode"], "HTML")

    def test_new_user_menu_uses_a_short_fallback_without_its_removed_caption(self):
        message = SimpleNamespace(
            reply_photo=AsyncMock(),
            reply_text=AsyncMock(),
        )
        query = SimpleNamespace(
            message=message,
            delete_message=AsyncMock(),
        )
        with patch.object(bot, "WELCOME_IMAGE_PATH", self.root / "missing.jpg"):
            asyncio.run(
                bot._show_new_user_menu(SimpleNamespace(callback_query=query))
            )
        self.assertEqual(
            message.reply_text.await_args.args[0],
            "Choose a topic below:",
        )

    def test_boost_caption_is_not_double_handled_by_ai_reply(self):
        message = SimpleNamespace(text=None, caption="boost 749488", reply_text=AsyncMock(), message_id=555, chat_id=100)
        chat = SimpleNamespace(id=100, type="private")
        user = SimpleNamespace(id=445, username="trader", first_name="Trader")
        update = SimpleNamespace(effective_message=message, effective_chat=chat, effective_user=user)
        app = SimpleNamespace(bot_data={"guide": MagicMock()})
        context = SimpleNamespace(application=app)
        with (
            patch.object(bot, "save_user_uid"),
            patch.object(bot, "reserve_boost_request", return_value=0),
            patch.object(bot, "record_boost_request"),
        ):
            asyncio.run(bot.boost_caption_handler(update, context))
            asyncio.run(bot.ai_reply(update, context))
        message.reply_text.assert_awaited_once()

    def test_resolve_timezone_supports_asia_kolkata(self):
        tz = bot.resolve_timezone("Asia/Kolkata")
        self.assertIsNotNone(tz)


if __name__ == "__main__":
    unittest.main()
