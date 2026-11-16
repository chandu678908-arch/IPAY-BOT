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

        image = Image.new("RGB", (480, 100), "white")
        ImageDraw.Draw(image).text(
            (12, 24),
            "ORDER LOCKING 100 QUOTA 104",
            font=ImageFont.load_default(),
            fill="black",
        )
        image = image.resize((1920, 400), Image.Resampling.NEAREST)
        image_bytes = BytesIO()
        image.save(image_bytes, format="PNG")

        text = bot._tesseract_ocr_bytes(image_bytes.getvalue())

        self.assertEqual(bot._classify_locked_screenshot(text), "order")

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
                    self.assertEqual(
                        sender.await_args.kwargs.get("reply_to_message_id"),
                        message_id,
                    )
                if is_transition:
                    gemini_confirm.assert_not_awaited()

        asyncio.run(run_cases())


if __name__ == "__main__":
    unittest.main()
