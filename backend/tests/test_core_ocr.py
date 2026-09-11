import unittest
from unittest.mock import patch

import fitz

from app.services import invoice_extraction_service


INVOICE_TEXT = """Green Solar Services Sdn Bhd
INVOICE
Invoice No: OCR-001
Invoice Date: 03/09/2026
Buyer: ABC Manufacturing Sdn Bhd
Description
1 Solar panel maintenance 1 UNIT 1,250.00 1,250.00
Total RM 1,250.00
Thank you for your business.
"""


def pdf_bytes(text: str | None = None) -> bytes:
    document = fitz.open()
    page = document.new_page()
    if text:
        page.insert_text((50, 60), text, fontsize=10)
    content = document.tobytes()
    document.close()
    return content


class CoreOcrAndExtractionTests(unittest.TestCase):
    def test_parser_extracts_required_invoice_fields(self):
        invoice, warnings = invoice_extraction_service._parse_invoice_text(
            INVOICE_TEXT,
            filename="OCR-001.pdf",
        )

        self.assertEqual(invoice.invoice_number, "OCR-001")
        self.assertEqual(invoice.supplier_name, "Green Solar Services Sdn Bhd")
        self.assertEqual(invoice.invoice_date, "2026-09-03")
        self.assertEqual(invoice.amount, 1250.0)
        self.assertEqual(invoice.description, "Solar panel maintenance")
        self.assertFalse(any("Could not confidently extract" in item for item in warnings))

    def test_native_pdf_uses_text_extraction(self):
        preview = invoice_extraction_service._preview_invoice_content(
            pdf_bytes(INVOICE_TEXT),
            "OCR-001.pdf",
        )

        self.assertEqual(preview.method, "pdf_text")
        self.assertEqual(preview.invoice.invoice_number, "OCR-001")
        self.assertEqual(preview.invoice.amount, 1250.0)

    @patch.object(invoice_extraction_service, "_extract_ocr_text")
    def test_scanned_pdf_falls_back_to_ocr(self, extract_ocr_text):
        extract_ocr_text.return_value = (INVOICE_TEXT, [])

        preview = invoice_extraction_service._preview_invoice_content(
            pdf_bytes(),
            "OCR-001.pdf",
        )

        self.assertEqual(preview.method, "ocr")
        self.assertEqual(preview.invoice.invoice_number, "OCR-001")
        extract_ocr_text.assert_called_once()


if __name__ == "__main__":
    unittest.main()
