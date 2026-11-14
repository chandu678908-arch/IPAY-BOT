import os
import re
import shutil
import unittest
from io import BytesIO
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

import bot


class TesseractIntegrationTests(unittest.TestCase):
    def _tesseract_command(self):
        configured = os.getenv("TESSERACT_CMD", "").strip()
        command = shutil.which(configured) if configured else shutil.which("tesseract")
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


if __name__ == "__main__":
    unittest.main()
