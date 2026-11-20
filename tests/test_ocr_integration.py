import asyncio
import os
import re
import unittest
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from PIL import Image, ImageDraw, ImageFont

import bot


class TesseractIntegrationTests(unittest.TestCase):
    def _tesseract_command(self):
        command = bot._resolve_tesseract_command()
        if not command:
            if os.getenv("REQUIRE_TESSERACT_TEST") == "1":
                self.fail("Tesseract is required for this OCR integration test")
            self.skipTest("Tesseract executable is not installed")
        return command

    def test_tesseract_executes_ocr_on_generated_test_image(self):
        self._tesseract_command()

        image = Image.new("RGB", (1400, 300), "white")
        draw = ImageDraw.Draw(image)
        font = ImageFont.load_default(size=24)
        lines = (
            "INR USDT",
            "09/11/2026 - 15/11/2026",
            "LOCKING 22:58:41",
            "INCOME ACTIVITY QUOTA BALANCE",
            "FEEDBACK RECEIVED WAITING FOR PROCESSING",
            "PROCEED",
        )
        for index, line in enumerate(lines):
            draw.text((12, 8 + index * 44), line, font=font, fill="black")
        image = image.resize((2800, 600), Image.Resampling.NEAREST)
        image_bytes = BytesIO()
        image.save(image_bytes, format="PNG")

        text = bot._tesseract_ocr_bytes(image_bytes.getvalue())

        self.assertEqual(bot._classify_locked_screenshot(text), "order")

    def test_generated_transition_screenshots_route_by_utr_through_handler(self):
        """Exercise real Tesseract and reply routing without private fixtures."""
        self._tesseract_command()

        cases = (
            (
                "order",
                bot.ORDER_IMAGE_REPLIES,
                (
                    "Transition Information",
                    "Task ID po_942159179",
                    "Order ID 6AF9EB19000F",
                    "UTR 631922777297",
                    "Expire Time 23:08:41 15/11/2026",
                    "Pay via bank name mobikwik",
                    "Payer VPA 9508737769@mbkns",
                    "Payer Card ID 942159_1794763668251",
                    "Payee Account 2697",
                    "Payee Name nithin",
                    "Payee IFSC 0151",
                    "Amount 330.00",
                    "Locking",
                ),
            ),
            (
                "withdrawal",
                bot.WITHDRAWAL_IMAGE_REPLIES,
                (
                    "Transition Information",
                    "Task ID pi_642273179",
                    "Order ID 6AFB2FAE0003",
                    "UTR",
                    "Expire Time 22:08:52 16/11/2026",
                    "Bank Name paytm business",
                    "Payee VPA paytm.s4q3jcv@pty",
                    "Payee Card ID 642273_179474957380",
                    "Payer Name Jane Garcia",
                    "Order Amt 500.00",
                    "Actual Amt 500.00",
                    "Received -500.00 INR",
                    "Locking",
                ),
            ),
        )

        async def run_cases():
            for index, (expected, replies, lines) in enumerate(cases):
                image = Image.new("RGB", (1400, 1200), "white")
                draw = ImageDraw.Draw(image)
                font = ImageFont.load_default(size=38)
                for row, line in enumerate(lines):
                    draw.text((40, 35 + row * 82), line, font=font, fill="#151515")
                encoded = BytesIO()
                image.save(encoded, format="JPEG", quality=86)
                image_bytes = encoded.getvalue()

                ocr_text = bot._tesseract_ocr_bytes(image_bytes)
                self.assertEqual(
                    bot._classify_screenshot(ocr_text),
                    expected,
                    msg=f"Generated {expected} transition OCR mismatch: {ocr_text}",
                )

                sender = AsyncMock()
                message_id = 889000 + index
                message = SimpleNamespace(
                    message_id=message_id,
                    photo=[SimpleNamespace(file_id=f"generated-{expected}")],
                    document=None,
                )
                update = SimpleNamespace(
                    effective_message=message,
                    effective_chat=SimpleNamespace(id=message_id, type="private"),
                    effective_user=SimpleNamespace(id=message_id + 100),
                )
                context = SimpleNamespace(
                    application=SimpleNamespace(bot_data={}),
                    bot=SimpleNamespace(send_message=sender),
                )
                with (
                    patch.object(
                        bot,
                        "_download_screenshot_bytes",
                        new=AsyncMock(return_value=image_bytes),
                    ),
                    patch.object(bot, "_claim_screenshot_reply", return_value=True),
                    patch.object(
                        bot, "_claim_screenshot_image_cooldown", return_value=True
                    ),
                    patch.object(bot, "_track_issue_spike", new=AsyncMock()),
                ):
                    await bot.group_screenshot_handler(update, context)

                sender.assert_awaited_once()
                selected_reply = sender.await_args.kwargs["text"]
                self.assertIn(selected_reply, replies)
                if expected == "order":
                    self.assertNotIn(selected_reply, bot.WITHDRAWAL_IMAGE_REPLIES)
                else:
                    self.assertNotIn(selected_reply, bot.ORDER_IMAGE_REPLIES)

        asyncio.run(run_cases())

    def test_generated_telegram_capture_routes_only_latest_forwarded_panel(self):
        self._tesseract_command()

        image = Image.new("RGB", (768, 1706), "#10202f")
        draw = ImageDraw.Draw(image)
        font = ImageFont.load_default(size=16)

        def draw_forwarded_panel(top, lines):
            draw.rounded_rectangle(
                (405, top, 748, top + 550),
                radius=12,
                fill="white",
                outline="#7657d5",
                width=4,
            )
            draw.rectangle((407, top, 746, top + 48), fill="#7657d5")
            draw.text(
                (420, top + 12),
                "Forwarded from Alice",
                font=font,
                fill="white",
            )
            for row, line in enumerate(lines):
                draw.text(
                    (420, top + 62 + row * 28),
                    line,
                    font=font,
                    fill="#151515",
                )

        draw_forwarded_panel(
            130,
            (
                "Transition Information",
                "Task ID po_942159179",
                "Order ID 6AF9EB19000F",
                "UTR 631922777297",
                "Payer VPA 9508737769",
                "Payer Card ID 942159179",
                "Payee Account 2697",
                "Payee Name Nithin",
                "Payee IFSC 0151",
                "Amount 330.00",
                "Locking",
            ),
        )
        draw_forwarded_panel(
            820,
            (
                "Transition Information",
                "Task ID pi_642273179",
                "Order ID 6AFB2FAE0003",
                "UTR 0",
                "Bank Name paytm business",
                "Payee VPA paytm.s4q3jcv",
                "Payee Card ID 642273179",
                "Payer Name Jane Garcia",
                "Order Amt 500.00",
                "Actual Amt 500.00",
                "Received -500.00 INR",
                "Locking",
            ),
        )
        encoded = BytesIO()
        image.save(encoded, format="PNG")
        image_bytes = encoded.getvalue()

        ocr_text = bot._tesseract_ocr_bytes(image_bytes)
        self.assertEqual(
            bot._classify_screenshot(ocr_text),
            "withdrawal",
            msg=f"Latest forwarded panel was not selected: {ocr_text}",
        )

        sender = AsyncMock()
        message_id = 889010
        message = SimpleNamespace(
            message_id=message_id,
            photo=[SimpleNamespace(file_id="generated-forwarded-panels")],
            document=None,
        )
        update = SimpleNamespace(
            effective_message=message,
            effective_chat=SimpleNamespace(id=message_id, type="private"),
            effective_user=SimpleNamespace(id=message_id + 100),
        )
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={}),
            bot=SimpleNamespace(send_message=sender),
        )
        with (
            patch.object(
                bot,
                "_download_screenshot_bytes",
                new=AsyncMock(return_value=image_bytes),
            ),
            patch.object(bot, "_claim_screenshot_reply", return_value=True),
            patch.object(
                bot, "_claim_screenshot_image_cooldown", return_value=True
            ),
            patch.object(bot, "_track_issue_spike", new=AsyncMock()),
        ):
            asyncio.run(bot.group_screenshot_handler(update, context))

        sender.assert_awaited_once()
        selected_reply = sender.await_args.kwargs["text"]
        self.assertIn(selected_reply, bot.WITHDRAWAL_IMAGE_REPLIES)
        self.assertNotIn(selected_reply, bot.ORDER_IMAGE_REPLIES)

    def test_tesseract_ocr_classifies_withdrawal_pending_reference_screenshot(self):
        self._tesseract_command()

        reference_path = os.getenv("WITHDRAWAL_PENDING_REFERENCE_IMAGE", "").strip()
        image_path = (
            Path(reference_path)
            if reference_path
            else Path(__file__).parent
            / "fixtures"
            / "withdrawal_pending_reference_redacted.png"
        )
        self.assertTrue(image_path.is_file(), "Withdrawal Pending reference image is missing")
        text = bot._tesseract_ocr_bytes(image_path.read_bytes())
        normalized = re.sub(r"[^a-z]+", " ", text.casefold())
        self.assertTrue(
            re.search(r"\bwithdraw(?:al)?\b", normalized),
            "Tesseract did not recognize the Withdrawal heading",
        )
        self.assertTrue(
            re.search(r"\bpending\b", normalized),
            "Tesseract did not recognize the Pending status",
        )
        self.assertEqual(bot._classify_screenshot(text), "withdrawal_pending")

    def test_actual_locked_screenshot_fixtures_reach_their_reply_collections(self):
        self._tesseract_command()
        image_root = Path(__file__).resolve().parents[1] / "test_images"
        cases = (
            ("02_order_locked_330.png", "order", bot.ORDER_IMAGE_REPLIES, False),
            (
                "01_withdrawal_locked_100.png",
                "withdrawal",
                bot.WITHDRAWAL_IMAGE_REPLIES,
                False,
            ),
            (
                "Analysis output 1.png",
                "order",
                bot.ORDER_IMAGE_REPLIES,
                True,
            ),
            (
                "Analysis output 2.png",
                "withdrawal",
                bot.WITHDRAWAL_IMAGE_REPLIES,
                True,
            ),
            (
                "01_WITHDRAWAL_LOCKED_transition_blank_UTR.png",
                "withdrawal",
                bot.WITHDRAWAL_IMAGE_REPLIES,
                True,
            ),
            (
                "02_WITHDRAWAL_LOCKED_withdrawal_list.png",
                "withdrawal",
                bot.WITHDRAWAL_IMAGE_REPLIES,
                False,
            ),
            (
                "03_ORDER_LOCKED_transition_populated_UTR.png",
                "order",
                bot.ORDER_IMAGE_REPLIES,
                True,
            ),
            (
                "04_ORDER_LOCKED_order_list.png",
                "order",
                bot.ORDER_IMAGE_REPLIES,
                False,
            ),
            ("03_payment_task.png", None, (), False),
            ("04_normal_dashboard.png", None, (), False),
        )
        missing = [name for name, *_ in cases if not (image_root / name).is_file()]
        if missing:
            self.skipTest(
                "Local screenshot fixtures are unavailable: " + ", ".join(missing)
            )

        async def run_cases():
            for index, (filename, expected, replies, is_transition) in enumerate(cases):
                image_bytes = (image_root / filename).read_bytes()
                ocr_text = bot._tesseract_ocr_bytes(image_bytes)
                self.assertEqual(
                    bot._classify_screenshot(ocr_text),
                    expected,
                    msg=f"OCR/classification mismatch for {filename}: {ocr_text}",
                )

                sender = AsyncMock()
                context = SimpleNamespace(
                    application=SimpleNamespace(
                        bot_data={"gemini_client": object()} if is_transition else {}
                    ),
                    bot=SimpleNamespace(
                        send_message=sender,
                        delete_message=AsyncMock(),
                    ),
                )
                message_id = 880000 + index
                message = SimpleNamespace(
                    message_id=message_id,
                    photo=[SimpleNamespace(file_id=f"fixture-{filename}")],
                    document=None,
                )
                update = SimpleNamespace(
                    effective_message=message,
                    effective_chat=SimpleNamespace(
                        id=-message_id,
                        type="supergroup",
                    ),
                    effective_user=SimpleNamespace(id=990000 + index),
                )
                with (
                    patch.object(
                        bot,
                        "_download_screenshot_bytes",
                        new=AsyncMock(return_value=image_bytes),
                    ),
                    patch.object(bot, "_claim_screenshot_reply", return_value=True),
                    patch.object(
                        bot, "_claim_screenshot_image_cooldown", return_value=True
                    ),
                    patch.object(bot, "_track_issue_spike", new=AsyncMock()),
                    patch.object(
                        bot,
                        "_gemini_confirms_screenshot_category",
                        new=AsyncMock(return_value=False),
                    ) as gemini_confirm,
                ):
                    await bot.group_screenshot_handler(update, context)

                if expected is None:
                    sender.assert_not_awaited()
                else:
                    sender.assert_awaited_once()
                    reply = sender.await_args.kwargs["text"]
                    self.assertIn(reply, replies)
                    if expected == "order":
                        self.assertNotIn(reply, bot.WITHDRAWAL_IMAGE_REPLIES)
                    elif expected == "withdrawal":
                        self.assertNotIn(reply, bot.ORDER_IMAGE_REPLIES)
                    self.assertEqual(
                        sender.await_args.kwargs.get("reply_to_message_id"),
                        message_id,
                    )
                if is_transition:
                    gemini_confirm.assert_not_awaited()

        asyncio.run(run_cases())


if __name__ == "__main__":
    unittest.main()
