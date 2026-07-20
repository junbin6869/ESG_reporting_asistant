import json
import unittest
from unittest.mock import patch

import mcp_server
from app.schemas import (
    AgentRun,
    AgentStep,
    DashboardSummary,
    EvidenceItem,
    GenerateReportRequest,
    GeneratedReport,
    InvoiceClassification,
    InvoiceRow,
    SummaryBucket,
)


class McpServerTests(unittest.TestCase):
    def setUp(self):
        self.request = GenerateReportRequest(
            period_start="2026-01-01",
            period_end="2026-12-31",
            title="Test ESG Report",
        )
        self.run = AgentRun(
            id="run-1",
            status="queued",
            request=self.request,
            steps=[
                AgentStep(
                    id="prepare_context",
                    label="Preparing reviewed invoices",
                    status="queued",
                )
            ],
            created_at="2026-01-01T00:00:00+00:00",
            updated_at="2026-01-01T00:00:00+00:00",
        )

    @patch.object(mcp_server, "retrieve_guidelines")
    def test_search_guidelines_returns_structured_evidence(self, retrieve_guidelines):
        retrieve_guidelines.return_value = [
            EvidenceItem(
                chunk_id="chunk-1",
                source="guideline.pdf",
                section="Energy",
                topic="Environmental",
                page=4,
                supporting_text="Track electricity consumption.",
            )
        ]

        result = mcp_server.search_esg_guidelines_impl("energy use", top_k=2)

        self.assertEqual(result[0]["chunk_id"], "chunk-1")
        retrieve_guidelines.assert_called_once_with("energy use", top_k=2)

    def test_search_guidelines_rejects_invalid_top_k(self):
        with self.assertRaisesRegex(ValueError, "between 1 and 5"):
            mcp_server.search_esg_guidelines_impl("energy", top_k=6)

    @patch.object(mcp_server, "get_dashboard_summary")
    def test_get_summary_returns_structured_output(self, get_dashboard_summary):
        bucket = SummaryBucket(count=1, amount=100.0, currency="MYR")
        get_dashboard_summary.return_value = DashboardSummary(
            period={"label": "2026-Q1", "start": "2026-01-01", "end": "2026-03-31"},
            total_invoices=bucket,
            categories={
                "Environmental": bucket,
                "Social": bucket,
                "Governance": bucket,
                "Non-ESG": bucket,
            },
        )

        result = mcp_server.get_esg_summary_impl("2026-01-01", "2026-03-31")

        self.assertEqual(result["total_invoices"]["count"], 1)
        get_dashboard_summary.assert_called_once_with("2026-01-01", "2026-03-31")

    @patch.object(mcp_server, "list_invoices")
    def test_list_reviewed_invoices_enforces_period_and_status(self, list_invoices):
        reviewed = InvoiceClassification(
            category="Environmental",
            status="Reviewed",
            confidence="high",
            reason="Reviewed",
        )
        pending = InvoiceClassification(
            category="Social",
            status="Pending",
            confidence="medium",
            reason="Pending",
        )
        list_invoices.return_value = [
            self._invoice("inside-reviewed", "2026-06-01", reviewed),
            self._invoice("inside-pending", "2026-06-01", pending),
            self._invoice("outside-reviewed", "2025-06-01", reviewed),
        ]

        result = mcp_server.list_reviewed_invoices_impl("2026-01-01", "2026-12-31")

        self.assertEqual([invoice["id"] for invoice in result], ["inside-reviewed"])

    @patch.object(mcp_server.settings, "openai_api_key", "test-key")
    @patch.object(mcp_server, "start_report_agent")
    def test_start_report_uses_existing_agent(self, start_report_agent):
        start_report_agent.return_value = self.run

        result = mcp_server.start_esg_report_impl(
            "2026-01-01",
            "2026-12-31",
            " Test ESG Report ",
        )

        self.assertEqual(result["id"], "run-1")
        request = start_report_agent.call_args.args[0]
        self.assertEqual(request.title, "Test ESG Report")

    @patch.object(mcp_server.settings, "openai_api_key", "")
    @patch.object(mcp_server, "start_report_agent")
    def test_start_report_rejects_missing_api_key(self, start_report_agent):
        with self.assertRaisesRegex(ValueError, "OPENAI_API_KEY"):
            mcp_server.start_esg_report_impl(
                "2026-01-01",
                "2026-12-31",
                "Test ESG Report",
            )

        start_report_agent.assert_not_called()

    @patch.object(mcp_server, "get_report_agent_run")
    def test_get_report_run_status_returns_existing_run(self, get_report_agent_run):
        get_report_agent_run.return_value = self.run

        result = mcp_server.get_report_run_status_impl("run-1")

        self.assertEqual(result["status"], "queued")

    @patch.object(mcp_server, "get_report_agent_run", return_value=None)
    def test_get_report_run_status_rejects_unknown_run(self, _get_report_agent_run):
        with self.assertRaisesRegex(ValueError, "Agent run not found"):
            mcp_server.get_report_run_status_impl("missing")

    @patch.object(mcp_server, "get_invoice")
    def test_invoice_resource_returns_json(self, get_invoice):
        get_invoice.return_value = self._invoice(
            "invoice-1",
            "2026-06-01",
            InvoiceClassification(
                category="Environmental",
                status="Reviewed",
                confidence="high",
                reason="Reviewed",
            ),
        )

        result = json.loads(mcp_server.get_invoice_resource_impl("invoice-1"))

        self.assertEqual(result["id"], "invoice-1")

    @patch.object(mcp_server, "get_report")
    def test_report_resource_returns_json(self, get_report):
        get_report.return_value = GeneratedReport(
            id="report-1",
            title="Report",
            period_start="2026-01-01",
            period_end="2026-12-31",
            status="Draft",
            content="Report content",
            created_at="2026-01-01T00:00:00+00:00",
        )

        result = json.loads(mcp_server.get_report_resource_impl("report-1"))

        self.assertEqual(result["id"], "report-1")

    @patch.object(mcp_server, "get_invoice", return_value=None)
    def test_invoice_resource_rejects_unknown_invoice(self, _get_invoice):
        with self.assertRaisesRegex(ValueError, "Invoice not found"):
            mcp_server.get_invoice_resource_impl("missing")

    def test_period_validation_rejects_invalid_dates_and_order(self):
        with self.assertRaisesRegex(ValueError, "YYYY-MM-DD"):
            mcp_server.get_esg_summary_impl("2026/01/01", "2026-12-31")
        with self.assertRaisesRegex(ValueError, "on or before"):
            mcp_server.get_esg_summary_impl("2026-12-31", "2026-01-01")

    @patch.object(mcp_server, "log_mcp_event")
    def test_validation_failure_is_audited(self, log_mcp_event):
        with self.assertRaisesRegex(ValueError, "between 1 and 5"):
            mcp_server.search_esg_guidelines_impl("energy", top_k=0)

        log_mcp_event.assert_called_once_with(
            "search_esg_guidelines",
            "failed",
            error_type="ValueError",
        )

    @patch.object(mcp_server, "log_mcp_event")
    def test_failed_operation_audit_does_not_include_business_content(
        self,
        log_mcp_event,
    ):
        with self.assertRaisesRegex(RuntimeError, "sensitive guideline text"):
            mcp_server._run_audited(
                "search_esg_guidelines",
                lambda: (_ for _ in ()).throw(RuntimeError("sensitive guideline text")),
            )

        log_mcp_event.assert_called_once_with(
            "search_esg_guidelines",
            "failed",
            error_type="RuntimeError",
        )

    @staticmethod
    def _invoice(
        invoice_id: str,
        invoice_date: str,
        classification: InvoiceClassification,
    ) -> InvoiceRow:
        return InvoiceRow(
            id=invoice_id,
            invoice_number=invoice_id,
            supplier_name="Supplier",
            invoice_date=invoice_date,
            amount=100,
            currency="MYR",
            description="Description",
            processed_by_llm=True,
            classification=classification,
        )


if __name__ == "__main__":
    unittest.main()
