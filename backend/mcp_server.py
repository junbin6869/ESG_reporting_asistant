import sys
from contextlib import redirect_stdout
from datetime import date
from typing import Any, Callable, TypeVar

from mcp.server.fastmcp import FastMCP

from app.agents.report_agent import get_report_agent_run, start_report_agent
from app.core.config import settings
from app.core.mcp_audit import log_mcp_event
from app.db.database import init_db
from app.schemas import GenerateReportRequest
from app.services.invoice_service import get_invoice, list_invoices
from app.services.rag_service import retrieve_guidelines
from app.services.report_service import get_report
from app.services.summary_service import get_dashboard_summary


T = TypeVar("T")

mcp = FastMCP(
    "ESG Assistant",
    instructions=(
        "Use these tools to search local ESG guidelines, inspect reviewed invoices, "
        "summarize ESG activity, and generate evidence-led ESG draft reports."
    ),
    json_response=True,
)


def _require_text(value: str, field_name: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError(f"{field_name} must not be empty.")
    return cleaned


def _parse_date(value: str, field_name: str) -> str:
    cleaned = _require_text(value, field_name)
    try:
        return date.fromisoformat(cleaned).isoformat()
    except ValueError as exc:
        raise ValueError(f"{field_name} must use YYYY-MM-DD format.") from exc


def _validate_period(period_start: str, period_end: str) -> tuple[str, str]:
    start = _parse_date(period_start, "period_start")
    end = _parse_date(period_end, "period_end")
    if start > end:
        raise ValueError("period_start must be on or before period_end.")
    return start, end


def _run_audited(operation: str, callback: Callable[[], T]) -> T:
    try:
        # MCP stdio reserves stdout for protocol messages.
        with redirect_stdout(sys.stderr):
            result = callback()
    except Exception as exc:
        log_mcp_event(operation, "failed", error_type=type(exc).__name__)
        raise
    log_mcp_event(operation, "succeeded")
    return result


def search_esg_guidelines_impl(query: str, top_k: int = 3) -> list[dict[str, Any]]:
    def execute() -> list[dict[str, Any]]:
        cleaned_query = _require_text(query, "query")
        if not 1 <= top_k <= 5:
            raise ValueError("top_k must be between 1 and 5.")
        evidence = retrieve_guidelines(cleaned_query, top_k=top_k)
        return [item.model_dump(mode="json") for item in evidence]

    return _run_audited("search_esg_guidelines", execute)


def get_esg_summary_impl(period_start: str, period_end: str) -> dict[str, Any]:
    def execute() -> dict[str, Any]:
        start, end = _validate_period(period_start, period_end)
        summary = get_dashboard_summary(start, end)
        return summary.model_dump(mode="json")

    return _run_audited("get_esg_summary", execute)


def list_reviewed_invoices_impl(
    period_start: str,
    period_end: str,
) -> list[dict[str, Any]]:
    def execute() -> list[dict[str, Any]]:
        start, end = _validate_period(period_start, period_end)
        invoices = [
            invoice
            for invoice in list_invoices()
            if start <= invoice.invoice_date <= end
            and invoice.classification
            and invoice.classification.status == "Reviewed"
        ]
        return [invoice.model_dump(mode="json") for invoice in invoices]

    return _run_audited("list_reviewed_invoices", execute)


def start_esg_report_impl(
    period_start: str,
    period_end: str,
    title: str,
) -> dict[str, Any]:
    def execute() -> dict[str, Any]:
        start, end = _validate_period(period_start, period_end)
        cleaned_title = _require_text(title, "title")
        if not settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY is not configured.")
        request = GenerateReportRequest(
            period_start=start,
            period_end=end,
            title=cleaned_title,
        )
        run = start_report_agent(request)
        return run.model_dump(mode="json")

    return _run_audited("start_esg_report", execute)


def get_report_run_status_impl(run_id: str) -> dict[str, Any]:
    def execute() -> dict[str, Any]:
        cleaned_run_id = _require_text(run_id, "run_id")
        run = get_report_agent_run(cleaned_run_id)
        if not run:
            raise ValueError(f"Agent run not found: {cleaned_run_id}")
        return run.model_dump(mode="json")

    return _run_audited("get_report_run_status", execute)


def get_invoice_resource_impl(invoice_id: str) -> str:
    def execute() -> str:
        cleaned_invoice_id = _require_text(invoice_id, "invoice_id")
        invoice = get_invoice(cleaned_invoice_id)
        if not invoice:
            raise ValueError(f"Invoice not found: {cleaned_invoice_id}")
        return invoice.model_dump_json(indent=2)

    return _run_audited("get_invoice_resource", execute)


def get_report_resource_impl(report_id: str) -> str:
    def execute() -> str:
        cleaned_report_id = _require_text(report_id, "report_id")
        report = get_report(cleaned_report_id)
        if not report:
            raise ValueError(f"Report not found: {cleaned_report_id}")
        return report.model_dump_json(indent=2)

    return _run_audited("get_report_resource", execute)


@mcp.tool()
def search_esg_guidelines(query: str, top_k: int = 3) -> list[dict[str, Any]]:
    """Search the local ESG guideline vector database for supporting evidence."""
    return search_esg_guidelines_impl(query, top_k)


@mcp.tool()
def get_esg_summary(period_start: str, period_end: str) -> dict[str, Any]:
    """Get ESG invoice totals and category breakdown for an inclusive date range."""
    return get_esg_summary_impl(period_start, period_end)


@mcp.tool()
def list_reviewed_invoices(
    period_start: str,
    period_end: str,
) -> list[dict[str, Any]]:
    """List only human-reviewed invoices within an inclusive date range."""
    return list_reviewed_invoices_impl(period_start, period_end)


@mcp.tool()
def start_esg_report(
    period_start: str,
    period_end: str,
    title: str,
) -> dict[str, Any]:
    """Start the existing autonomous ESG report agent for a reporting period."""
    return start_esg_report_impl(period_start, period_end, title)


@mcp.tool()
def get_report_run_status(run_id: str) -> dict[str, Any]:
    """Get the current status, steps, error, and result of an ESG report run."""
    return get_report_run_status_impl(run_id)


@mcp.resource("esg://invoices/{invoice_id}")
def get_invoice_resource(invoice_id: str) -> str:
    """Read one invoice and its latest classification."""
    return get_invoice_resource_impl(invoice_id)


@mcp.resource("esg://reports/{report_id}")
def get_report_resource(report_id: str) -> str:
    """Read one generated ESG report."""
    return get_report_resource_impl(report_id)


def main() -> None:
    with redirect_stdout(sys.stderr):
        init_db()
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
