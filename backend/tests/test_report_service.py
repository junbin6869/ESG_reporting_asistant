import unittest
from unittest.mock import patch

import fitz

from app.core.report_format import (
    INVOICE_BASED_DISCLOSURE,
    MISSING_KPI_TEXT,
    PERFORMANCE_TABLE_HEADER,
    REPORT_HEADINGS,
    validate_report_content,
)
from app.schemas import EvidenceItem, GeneratedReport, InvoiceClassification, InvoiceRow
from app.services import report_service


VALID_REPORT = "\n".join(
    [
        REPORT_HEADINGS[0],
        INVOICE_BASED_DISCLOSURE,
        REPORT_HEADINGS[1],
        PERFORMANCE_TABLE_HEADER,
        "|---|---:|---:|---:|---:|",
        "| Environmental | 1 | MYR 100.00 | 100% | 100% |",
        REPORT_HEADINGS[2],
        MISSING_KPI_TEXT,
        REPORT_HEADINGS[3],
        MISSING_KPI_TEXT,
        REPORT_HEADINGS[4],
        MISSING_KPI_TEXT,
        REPORT_HEADINGS[5],
        "Theme narrative.",
        REPORT_HEADINGS[6],
        "guideline.pdf; section=Energy; page=1; chunk ID=chunk-1",
        REPORT_HEADINGS[7],
        MISSING_KPI_TEXT,
        REPORT_HEADINGS[8],
        "- Evidence collection: collect KPIs.",
        "- Operational actions: verify delivery.",
        "- Governance actions: approve controls.",
        "- Human review: validate the draft.",
    ]
)


class ReportServiceTests(unittest.TestCase):
    def test_reviewed_summary_groups_currency_share_and_evidence(self):
        invoices = [
            self._invoice("myr-1", "MYR", 100, "Environmental", with_evidence=True),
            self._invoice("myr-2", "MYR", 300, "Social"),
            self._invoice("usd-1", "USD", 50, "Environmental"),
        ]

        summary = report_service.build_reviewed_invoice_summary(invoices)

        self.assertIn("MYR 100.00 (25.0% of all reviewed MYR spend)", summary)
        self.assertIn("USD 50.00 (100.0% of all reviewed USD spend)", summary)
        self.assertIn("Invoice evidence coverage: 1/2 (50.0%)", summary)
        self.assertIn("chunk_id=chunk-1", summary)

    def test_report_contract_validates_required_disclosure(self):
        validate_report_content(VALID_REPORT)
        with self.assertRaisesRegex(ValueError, "required disclosure content"):
            validate_report_content(VALID_REPORT.replace(MISSING_KPI_TEXT, "Unknown"))

    def test_validation_accepts_markdown_and_case_variations(self):
        varied = (
            VALID_REPORT.replace(
                "- Evidence collection:",
                "- **Evidence Collection:**",
            )
            .replace("- Operational actions:", "- **OPERATIONAL ACTIONS:**")
            .replace("- Governance actions:", "- ### Governance Actions:")
            .replace("- Human review:", "- `Human Review`:")
        )

        validate_report_content(varied)

    def test_prompt_forbids_treating_spend_as_impact(self):
        prompt = report_service.build_report_prompt(
            payload=type(
                "Request",
                (),
                {
                    "title": "Report",
                    "period_start": "2026-01-01",
                    "period_end": "2026-12-31",
                },
            )(),
            invoice_summary="Reviewed invoices",
        )

        self.assertIn("Never describe invoice spend as achieved ESG impact", prompt)
        self.assertIn(PERFORMANCE_TABLE_HEADER, prompt)

    @patch.object(report_service, "list_invoices")
    def test_standard_report_summary_uses_reviewed_invoices_only(self, list_invoices):
        reviewed = self._invoice("reviewed", "MYR", 100, "Environmental")
        pending = reviewed.model_copy(deep=True)
        pending.id = "pending"
        pending.invoice_number = "pending"
        pending.classification.status = "Pending"
        list_invoices.return_value = [reviewed, pending]

        summary = report_service._format_invoice_summary("2026-01-01", "2026-12-31")

        self.assertIn("Invoice reviewed", summary)
        self.assertNotIn("Invoice pending", summary)

    @patch.object(report_service, "get_report")
    def test_pdf_contains_cover_sections_and_page_number(self, get_report):
        get_report.return_value = self._report(VALID_REPORT)

        pdf = report_service.build_report_pdf("report-1")
        document = fitz.open(stream=pdf, filetype="pdf")
        text = "\n".join(page.get_text() for page in document)

        self.assertIn("INVOICE-BASED ESG DISCLOSURE", text)
        self.assertIn("Performance Overview", text)
        self.assertIn("Page 1", text)

    @patch.object(report_service, "get_report")
    def test_pdf_supports_legacy_plain_text_report(self, get_report):
        get_report.return_value = self._report("Legacy report paragraph.\nAnother line.")

        pdf = report_service.build_report_pdf("report-1")
        document = fitz.open(stream=pdf, filetype="pdf")
        text = "\n".join(page.get_text() for page in document)

        self.assertIn("Legacy report paragraph.", text)

    @staticmethod
    def _invoice(
        invoice_id: str,
        currency: str,
        amount: float,
        category: str,
        with_evidence: bool = False,
    ) -> InvoiceRow:
        evidence = (
            [
                EvidenceItem(
                    chunk_id="chunk-1",
                    source="guideline.pdf",
                    section="Energy",
                    topic="Environmental",
                    page=1,
                    supporting_text="Evidence",
                )
            ]
            if with_evidence
            else []
        )
        return InvoiceRow(
            id=invoice_id,
            invoice_number=invoice_id,
            supplier_name="Supplier",
            invoice_date="2026-06-01",
            amount=amount,
            currency=currency,
            description="Description",
            processed_by_llm=True,
            classification=InvoiceClassification(
                category=category,
                status="Reviewed",
                confidence="high",
                reason="Reviewed",
                evidence=evidence,
            ),
        )

    @staticmethod
    def _report(content: str) -> GeneratedReport:
        return GeneratedReport(
            id="report-1",
            title="Invoice-Based ESG Draft Report",
            period_start="2026-01-01",
            period_end="2026-12-31",
            status="Draft",
            content=content,
            created_at="2026-01-01T00:00:00+00:00",
        )


if __name__ == "__main__":
    unittest.main()
