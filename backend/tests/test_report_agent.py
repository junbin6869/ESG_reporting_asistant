import json
import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from app.agents import report_agent
from app.db.database import init_db
from app.schemas import (
    AgentRun,
    AgentStep,
    EvidenceItem,
    GenerateReportRequest,
    GeneratedReport,
    InvoiceClassification,
    InvoiceRow,
)


REPORT_CONTENT = """
1. Disclosure Overview and Reporting Boundary
This is an invoice-based ESG draft disclosure. Reviewed invoice spend indicates expenditure signals and does not prove achieved ESG impact or performance.
2. Performance Overview
| Category | Reviewed invoices | Reviewed invoice spend | Share of reviewed spend | Evidence coverage |
|---|---:|---:|---:|---:|
| Environmental | 1 | MYR 100 | 100% | 100% |
3. Environmental Performance
Not available from invoice evidence
4. Social Performance
Not available from invoice evidence
5. Governance Performance
Not available from invoice evidence
6. Notable ESG Themes and Expenditure Narratives
Content.
7. Guideline Alignment and Traceability
guideline.pdf, section Environmental, page 1, chunk ID chunk-1
8. Data Quality, Limitations and Missing KPIs
Not available from invoice evidence
9. Forward Actions and Human Review
- Evidence collection: collect outcome KPIs.
- Operational actions: verify implementation.
- Governance actions: approve controls.
- Human review: validate this draft.
""".strip()


def tool_decision(call_id: str, query: str, top_k: int = 3):
    rationale = f"Need evidence for {query}."
    evidence_gaps = [f"{query} metrics"]
    request = report_agent.ToolRequest(
        id=call_id,
        name=report_agent.SEARCH_TOOL_NAME,
        arguments=json.dumps(
            {
                "query": query,
                "top_k": top_k,
                "rationale": rationale,
                "evidence_gaps": evidence_gaps,
            }
        ),
    )
    return report_agent.AgentDecision(
        content="",
        tool_requests=[request],
        assistant_message={
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": call_id,
                    "type": "function",
                    "function": {
                        "name": report_agent.SEARCH_TOOL_NAME,
                        "arguments": request.arguments,
                    },
                }
            ],
        },
    )


def report_decision():
    return report_agent.AgentDecision(
        content=REPORT_CONTENT,
        tool_requests=[],
        assistant_message={"role": "assistant", "content": REPORT_CONTENT},
    )


class ReportAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_database_path = report_agent.settings.database_path
        self.original_checkpoint_path = report_agent.settings.langgraph_checkpoint_path
        root = Path(self.temp_dir.name)
        report_agent.settings.database_path = root / "report-tests.db"
        report_agent.settings.langgraph_checkpoint_path = root / "report-checkpoints.db"
        init_db()
        report_agent._runs.clear()
        self.request = GenerateReportRequest(
            title="Test ESG Report",
            period_start="2026-01-01",
            period_end="2026-12-31",
        )
        self.run_id = f"test-run-{uuid.uuid4()}"
        report_agent._runs[self.run_id] = AgentRun(
            id=self.run_id,
            status="running",
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
        self.saved_report = GeneratedReport(
            id="report-1",
            title=self.request.title,
            period_start=self.request.period_start,
            period_end=self.request.period_end,
            status="Draft",
            content=REPORT_CONTENT,
            created_at="2026-01-01T00:00:00+00:00",
        )

    def tearDown(self):
        report_agent.close_report_graph()
        report_agent._runs.clear()
        report_agent.settings.database_path = self.original_database_path
        report_agent.settings.langgraph_checkpoint_path = self.original_checkpoint_path
        self.temp_dir.cleanup()

    @patch.object(report_agent, "save_report")
    @patch.object(report_agent, "retrieve_guidelines")
    @patch.object(report_agent, "_request_agent_decision")
    @patch.object(report_agent, "log_agent_event")
    @patch.object(report_agent, "_get_reviewed_invoices", return_value=[])
    def test_agent_can_search_multiple_times_then_finish(
        self,
        _get_invoices,
        log_agent_event,
        request_decision,
        retrieve_guidelines,
        save_report,
    ):
        request_decision.side_effect = [
            tool_decision("call-1", "energy disclosure"),
            tool_decision("call-2", "employee safety"),
            report_decision(),
        ]
        retrieve_guidelines.side_effect = [
            [self._evidence("chunk-1", "Energy evidence")],
            [
                self._evidence("chunk-1", "Energy evidence"),
                self._evidence("chunk-2", "Safety evidence"),
            ],
        ]
        save_report.return_value = self.saved_report

        result = report_agent._run_autonomous_agent(self.run_id, self.request)

        self.assertEqual(result.id, "report-1")
        self.assertEqual(retrieve_guidelines.call_count, 2)
        search_steps = [
            step
            for step in report_agent._runs[self.run_id].steps
            if step.label == "Searching local ESG guidelines"
        ]
        self.assertEqual(len(search_steps), 2)
        self.assertIn("1 duplicate", search_steps[1].detail)
        started_events = [
            call
            for call in log_agent_event.call_args_list
            if call.args[1] == "esg_search_started"
        ]
        self.assertEqual(len(started_events), 2)
        self.assertEqual(
            started_events[0].kwargs["rationale"],
            "Need evidence for energy disclosure.",
        )
        serialized_events = json.dumps(
            [
                {"args": call.args, "kwargs": call.kwargs}
                for call in log_agent_event.call_args_list
            ]
        )
        self.assertNotIn("Energy evidence", serialized_events)

    @patch.object(report_agent, "save_report")
    @patch.object(report_agent, "retrieve_guidelines")
    @patch.object(report_agent, "_request_agent_decision", return_value=report_decision())
    @patch.object(report_agent, "_get_reviewed_invoices", return_value=[])
    def test_agent_can_finish_without_searching(
        self,
        _get_invoices,
        _request_decision,
        retrieve_guidelines,
        save_report,
    ):
        save_report.return_value = self.saved_report

        report_agent._run_autonomous_agent(self.run_id, self.request)

        retrieve_guidelines.assert_not_called()
        save_report.assert_called_once()

    @patch.object(report_agent, "save_report")
    @patch.object(report_agent, "retrieve_guidelines", return_value=[])
    @patch.object(report_agent, "_request_agent_decision")
    @patch.object(report_agent, "_get_reviewed_invoices", return_value=[])
    def test_agent_stops_offering_tool_after_eight_searches(
        self,
        _get_invoices,
        request_decision,
        retrieve_guidelines,
        save_report,
    ):
        decisions = [
            tool_decision(f"call-{index}", f"query {index}")
            for index in range(report_agent.MAX_SEARCH_CALLS)
        ]
        decisions.append(report_decision())
        request_decision.side_effect = decisions
        save_report.return_value = self.saved_report

        report_agent._run_autonomous_agent(self.run_id, self.request)

        self.assertEqual(retrieve_guidelines.call_count, report_agent.MAX_SEARCH_CALLS)
        self.assertFalse(request_decision.call_args_list[-1].kwargs["allow_search"])
        saved_content = save_report.call_args.args[1]
        self.assertIn("used all 8 available guideline searches", saved_content)

    @patch.object(report_agent, "retrieve_guidelines")
    def test_unknown_tool_is_rejected_without_using_search_budget(self, retrieve_guidelines):
        request = report_agent.ToolRequest(
            id="unknown-1",
            name="web_search",
            arguments='{"query": "ESG"}',
        )

        result, detail, executed = report_agent._execute_search_tool(
            request,
            seen_chunk_ids=set(),
            search_calls=0,
        )

        self.assertFalse(executed)
        self.assertIn("Unknown tool", result["error"])
        self.assertEqual(detail, result["error"])
        retrieve_guidelines.assert_not_called()

    @patch.object(report_agent, "retrieve_guidelines")
    def test_invalid_tool_arguments_are_rejected(self, retrieve_guidelines):
        request = report_agent.ToolRequest(
            id="invalid-1",
            name=report_agent.SEARCH_TOOL_NAME,
            arguments="{invalid json",
        )

        result, _detail, executed = report_agent._execute_search_tool(
            request,
            seen_chunk_ids=set(),
            search_calls=0,
        )

        self.assertFalse(executed)
        self.assertIn("valid JSON", result["error"])
        retrieve_guidelines.assert_not_called()

    @patch.object(report_agent, "retrieve_guidelines")
    def test_search_requires_auditable_reason(self, retrieve_guidelines):
        request = report_agent.ToolRequest(
            id="missing-reason",
            name=report_agent.SEARCH_TOOL_NAME,
            arguments='{"query": "energy", "top_k": 3}',
        )

        result, _detail, executed = report_agent._execute_search_tool(
            request,
            seen_chunk_ids=set(),
            search_calls=0,
        )

        self.assertFalse(executed)
        self.assertIn("rationale", result["error"])
        retrieve_guidelines.assert_not_called()

    @patch.object(report_agent, "_run_autonomous_agent", side_effect=RuntimeError("LLM failed"))
    def test_execution_failure_marks_run_failed(self, _run_agent):
        report_agent._execute_report_agent(self.run_id)

        run = report_agent._runs[self.run_id]
        self.assertEqual(run.status, "failed")
        self.assertEqual(run.error, "LLM failed")

    def test_reviewed_invoice_loading_enforces_period_and_status(self):
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
        invoices = [
            self._invoice("inside-reviewed", "2026-06-01", reviewed),
            self._invoice("inside-pending", "2026-06-01", pending),
            self._invoice("outside-reviewed", "2025-06-01", reviewed),
        ]

        with patch.object(report_agent, "list_invoices", return_value=invoices):
            result = report_agent._get_reviewed_invoices("2026-01-01", "2026-12-31")

        self.assertEqual([invoice.id for invoice in result], ["inside-reviewed"])

    def test_report_validation_rejects_missing_sections(self):
        with self.assertRaisesRegex(ValueError, "missing required section headings"):
            report_agent._validate_report_content(
                "1. Disclosure Overview and Reporting Boundary"
            )

    def test_persistent_graph_checkpoints_and_restores_run_status(self):
        report_agent.close_report_graph()
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            database_path = root / "agent-test.db"
            checkpoint_path = root / "agent-checkpoints.db"
            with (
                patch.object(report_agent.settings, "database_path", database_path),
                patch.object(
                    report_agent.settings,
                    "langgraph_checkpoint_path",
                    checkpoint_path,
                ),
                patch.object(
                    report_agent,
                    "_request_agent_decision",
                    return_value=report_decision(),
                ),
                patch.object(report_agent, "_get_reviewed_invoices", return_value=[]),
            ):
                init_db()
                run_id = "persistent-test-run"
                now = "2026-01-01T00:00:00+00:00"
                report_agent._store_run(
                    AgentRun(
                        id=run_id,
                        status="queued",
                        request=self.request,
                        steps=[
                            AgentStep(
                                id="prepare_context",
                                label="Preparing reviewed invoices",
                                status="queued",
                            )
                        ],
                        created_at=now,
                        updated_at=now,
                    )
                )

                report_agent._execute_report_agent(run_id)
                run = report_agent.get_report_agent_run(run_id)
                self.assertIsNotNone(run)
                self.assertEqual(run.status, "completed")
                self.assertIsNotNone(run.report)

                connection = sqlite3.connect(database_path)
                try:
                    status = connection.execute(
                        "SELECT status FROM report_agent_runs WHERE id = ?",
                        (run_id,),
                    ).fetchone()[0]
                finally:
                    connection.close()
                self.assertEqual(status, "completed")
                self.assertTrue(checkpoint_path.exists())
                report_agent.close_report_graph()
        report_agent._runs.clear()

    @staticmethod
    def _evidence(chunk_id: str, text: str):
        return EvidenceItem(
            chunk_id=chunk_id,
            source="guideline.pdf",
            section="Environmental",
            topic="Topic",
            page=1,
            supporting_text=text,
        )

    @staticmethod
    def _invoice(
        invoice_id: str,
        invoice_date: str,
        classification: InvoiceClassification,
    ):
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
