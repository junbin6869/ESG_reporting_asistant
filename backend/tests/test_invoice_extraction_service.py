import unittest
from unittest.mock import MagicMock, patch

from app.core.config import _language_setting
from app.services.invoice_extraction_service import _extract_ocr_text


class InvoiceOcrTests(unittest.TestCase):
    @patch.dict("os.environ", {"TEST_OCR_LANGUAGES": " eng + msa + chi_sim "})
    def test_language_setting_normalizes_tesseract_language_list(self) -> None:
        self.assertEqual(
            _language_setting("TEST_OCR_LANGUAGES", "eng"),
            "eng+msa+chi_sim",
        )

    @patch("app.services.invoice_extraction_service.settings.ocr_languages", "eng+msa")
    @patch("PIL.Image.open")
    @patch("pytesseract.image_to_string", return_value="OCR result")
    @patch("pytesseract.get_tesseract_version", return_value="5.3.0")
    def test_configured_languages_are_passed_to_tesseract(
        self,
        _version: MagicMock,
        image_to_string: MagicMock,
        image_open: MagicMock,
    ) -> None:
        page = MagicMock()
        page.get_pixmap.return_value.tobytes.return_value = b"not-a-real-png"
        image_open.return_value.__enter__.return_value = MagicMock()

        text, warnings = _extract_ocr_text([page])

        self.assertEqual(text, "OCR result")
        self.assertEqual(warnings, [])
        self.assertEqual(image_to_string.call_args.kwargs["lang"], "eng+msa")

    @patch("pytesseract.get_tesseract_version", side_effect=OSError("not found"))
    def test_missing_tesseract_returns_a_helpful_warning(
        self,
        _version: MagicMock,
    ) -> None:
        text, warnings = _extract_ocr_text([])

        self.assertEqual(text, "")
        self.assertTrue(any("Tesseract OCR is not installed" in item for item in warnings))


if __name__ == "__main__":
    unittest.main()
