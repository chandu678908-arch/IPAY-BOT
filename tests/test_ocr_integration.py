import os
import shutil
import unittest
from io import BytesIO

from PIL import Image, ImageDraw, ImageFont

import bot


class TesseractIntegrationTests(unittest.TestCase):
    def test_tesseract_executes_ocr_on_generated_test_image(self):
        configured = os.getenv("TESSERACT_CMD", "").strip()
        command = shutil.which(configured) if configured else shutil.which("tesseract")
        if not command:
            if os.getenv("REQUIRE_TESSERACT_TEST") == "1":
                self.fail("Tesseract is required for this OCR integration test")
            self.skipTest("Tesseract executable is not installed")

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


if __name__ == "__main__":
    unittest.main()
