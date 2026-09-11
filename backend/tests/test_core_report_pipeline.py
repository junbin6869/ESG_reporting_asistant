import unittest
from unittest.mock import patch

import fitz
from langchain_core.documents import Document

from app.core.report_format import (
    INVOICE_BASED_DISCLOSURE,
    PERFORMANCE_TABLE_HEADER,
    REPORT_HEADINGS,
    validate_report_content,
)
from app.schemas import (
    EvidenceItem,
    GenerateReportRequest,
    GeneratedReport,
    InvoiceClassification,
    InvoiceRow,
)
from app.services import report_service


VALID_REPORT = "\n".join(
    [
        REPORT_HEADINGS[0],
        INVOICE_BASED_DISCLOSURE,
        REPORT_HEADINGS[1],
        PERFORMANCE_TABLE_HEADER,
        "|---|---:|---:|---:|",
        "| Environmental | 1 | MYR 100.00 | 100% |",
        REPORT_HEADINGS[2],
        "### Observed Themes",
        "- Environmental management expenditure [INV-GRP:E-01].",
        REPORT_HEADINGS[3],
        "### Observed Themes",
        REPORT_HEADINGS[4],
        "### Observed Themes",
        REPORT_HEADINGS[5],
        "Theme narrative.",
    ]
)


class CoreReportPipelineTests(unittest.TestCase):
    def test_reviewed_summary_contains_currency_share_and_traceability(self):
        invoices = [
            self._invoice("myr-1", "MYR", 100, "Environmental", True),
            self._invoice("myr-2", "MYR", 300, "Social"),
        ]

        summary = report_service.build_reviewed_invoice_summary(invoices)

        self.assertIn("MYR 100.00 (25.0% of all reviewed MYR spend)", summary)
        self.assertIn("[INV-GRP:E-01]", summary)
        self.assertIn("{{GUIDELINE:chunk-1}}", summary)

    @patch.object(report_service, "get_guideline_document_by_id")
    def test_guideline_marker_is_exactly_validated_and_rendered(self, exact_lookup):
        exact_lookup.return_value = self._guideline_document()
        internal_content = f"{VALID_REPORT}\nEnergy disclosure. {{{{GUIDELINE:chunk-1}}}}"

        public_content, audit_records = (
            report_service.validate_and_render_guideline_citations(
                internal_content,
                {"chunk-1"},
            )
        )

        exact_lookup.assert_called_once_with("chunk-1")
        self.assertNotIn("{{GUIDELINE:", public_content)
        self.assertIn("Source: guideline.pdf", public_content)
        self.assertEqual(audit_records[0]["validation_status"], "Passed")

    def test_invoice_citations_must_belong_to_frozen_snapshot(self):
        invoice = self._invoice("invoice-1", "MYR", 100, "Environmental")
        content = f"{VALID_REPORT}\n[INV-GRP:E-01] [INV:invoice-1]"

        citations = report_service.validate_report_invoice_citations(
            content,
            [invoice],
            period_start="2026-01-01",
            period_end="2026-12-31",
        )
        self.assertEqual(citations, {"INV-GRP:E-01", "INV:invoice-1"})

        with self.assertRaisesRegex(ValueError, "unknown or ineligible"):
            report_service.validate_report_invoice_citations(
                f"{content} [INV:not-in-snapshot]",
                [invoice],
                period_start="2026-01-01",
                period_end="2026-12-31",
            )

    def test_report_contract_rejects_missing_required_disclosure(self):
        validate_report_content(VALID_REPORT)
        with self.assertRaisesRegex(ValueError, "required disclosure content"):
            validate_report_content(VALID_REPORT.replace("Observed Themes", ""))

    @patch.object(report_service, "save_report")
    @patch.object(report_service, "generate_report_content")
    @patch.object(report_service, "list_invoices")
    def test_generation_saves_the_same_reviewed_invoice_snapshot(
        self,
        list_invoices,
        generate_report_content,
        save_report,
    ):
        invoice = self._invoice("invoice-1", "MYR", 100, "Environmental")
        invoice.version = 3
        list_invoices.return_value = [invoice]
        generate_report_content.return_value = (
            f"{VALID_REPORT}\n[INV-GRP:E-01] [INV:invoice-1]"
        )
        save_report.return_value = self._report(generate_report_content.return_value)

        result = report_service.generate_report(
            GenerateReportRequest(
                title="Core Report Test",
                period_start="2026-01-01",
                period_end="2026-12-31",
            )
        )

        self.assertEqual(result.id, "report-1")
        snapshot = save_report.call_args.kwargs["evidence_register_invoices"]
        self.assertEqual([(item.id, item.version) for item in snapshot], [("invoice-1", 3)])

    @patch.object(report_service, "get_report")
    def test_pdf_export_contains_cover_sections_and_page_number(self, get_report):
        get_report.return_value = self._report(VALID_REPORT)

        pdf = report_service.build_report_pdf("report-1")
        document = fitz.open(stream=pdf, filetype="pdf")
        text = "\n".join(page.get_text() for page in document)
        document.close()

        self.assertIn("INVOICE-BASED ESG DISCLOSURE", text)
        self.assertIn("Performance Overview", text)
        self.assertIn("Page 1", text)

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
            supplier_name="Supplier Sdn Bhd",
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

    @staticmethod
    def _guideline_document() -> Document:
        return Document(
            id="chunk-1",
            page_content="Organisations disclose energy consumption data.",
            metadata={
                "chunk_id": "chunk-1",
                "source": "guideline.pdf",
                "section_title": "Environmental (E)",
                "topic": "Energy",
                "page_start": 5,
                "page_end": 5,
                "source_version": "sha256-v1",
                "index_version": "index-v1",
            },
        )


if __name__ == "__main__":
    unittest.main()
