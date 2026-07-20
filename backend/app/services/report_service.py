import uuid
import re
from collections import defaultdict
from io import BytesIO

from fastapi import HTTPException
import fitz
from langchain_core.messages import HumanMessage, SystemMessage

from app.core.config import settings
from app.core.llm import get_chat_model
from app.core.report_format import report_contract, validate_report_content
from app.db.database import db_session, utc_now
from app.schemas import GenerateReportRequest, GeneratedReport, InvoiceRow
from app.services.invoice_service import list_invoices


def _format_invoice_summary(period_start: str, period_end: str) -> str:
    invoices = [
        invoice
        for invoice in list_invoices()
        if period_start <= invoice.invoice_date <= period_end
        and invoice.classification
        and invoice.classification.status == "Reviewed"
    ]
    return build_reviewed_invoice_summary(invoices)


def build_reviewed_invoice_summary(invoices: list[InvoiceRow]) -> str:
    lines: list[str] = []
    totals_by_currency: dict[str, float] = defaultdict(float)
    for invoice in invoices:
        totals_by_currency[invoice.currency] += invoice.amount

    for category in ["Environmental", "Social", "Governance", "Non-ESG"]:
        category_invoices = [
            invoice
            for invoice in invoices
            if invoice.classification and invoice.classification.category == category
        ]
        lines.append(f"## {category}")
        lines.append(f"Reviewed invoice count: {len(category_invoices)}")
        category_by_currency: dict[str, float] = defaultdict(float)
        for invoice in category_invoices:
            category_by_currency[invoice.currency] += invoice.amount

        if category_by_currency:
            for currency, spend in sorted(category_by_currency.items()):
                total = totals_by_currency[currency]
                share = (spend / total * 100) if total else 0
                lines.append(
                    f"Reviewed invoice spend: {currency} {spend:.2f} "
                    f"({share:.1f}% of all reviewed {currency} spend)"
                )
        else:
            lines.append("Reviewed invoice spend: none")

        evidence_count = sum(
            1
            for invoice in category_invoices
            if invoice.classification and invoice.classification.evidence
        )
        coverage = (
            evidence_count / len(category_invoices) * 100
            if category_invoices
            else 0
        )
        lines.append(
            f"Invoice evidence coverage: {evidence_count}/{len(category_invoices)} "
            f"({coverage:.1f}%)"
        )

        for invoice in category_invoices:
            reason = invoice.classification.reason if invoice.classification else ""
            evidence_refs = []
            if invoice.classification:
                evidence_refs = [
                    (
                        f"{item.source}; section={item.section or 'not specified'}; "
                        f"page={item.page or 'not specified'}; chunk_id={item.chunk_id}"
                    )
                    for item in invoice.classification.evidence
                ]
            lines.append(
                f"- Invoice {invoice.invoice_number}: {invoice.description}; "
                f"reviewed invoice spend={invoice.currency} {invoice.amount:.2f}; "
                f"review reason={reason}; evidence references="
                f"{' | '.join(evidence_refs) if evidence_refs else 'none'}"
            )
        lines.append("")
    return "\n".join(lines).strip()


def build_report_prompt(
    payload: GenerateReportRequest,
    invoice_summary: str,
    guideline_context: str = "",
    context_evaluation: str = "",
) -> str:
    return f"""
You are an ESG reporting assistant.
Generate a concise ESG draft report based only on the reviewed invoice summary
and retrieved ESG guideline context. Do not invent activities that are not
present in the invoices. Be proactive and evidence-led: if a claim, category,
or recommendation is not clearly supported by the retrieved ESG context, state
the limitation and explain what evidence should be collected next instead of
guessing.

Report title: {payload.title}
Reporting period: {payload.period_start} to {payload.period_end}

Reviewed invoice summary:
{invoice_summary}

Retrieved ESG guideline context:
{guideline_context or "No additional guideline context was provided."}

Agent evidence coverage check:
{context_evaluation or "No separate coverage check was recorded."}

{report_contract()}
""".strip()


def generate_report_content(prompt: str) -> str:
    if not settings.openai_api_key:
        raise HTTPException(status_code=500, detail="OPENAI_API_KEY is not configured")

    response = get_chat_model(temperature=0).invoke(
        [
            SystemMessage(
                content="You write concise ESG draft reports for human review."
            ),
            HumanMessage(content=prompt),
        ]
    )
    if isinstance(response.content, str):
        return response.content.strip()
    return "\n".join(
        str(block.get("text") or "")
        for block in response.content
        if isinstance(block, dict) and block.get("type") in {"text", "output_text"}
    ).strip()


def save_report(
    payload: GenerateReportRequest,
    content: str,
    *,
    source_run_id: str | None = None,
) -> GeneratedReport:
    if source_run_id:
        with db_session() as db:
            existing = db.execute(
                "SELECT id FROM reports WHERE source_run_id = ?",
                (source_run_id,),
            ).fetchone()
        if existing:
            saved = get_report(existing["id"])
            if saved:
                return saved

    report_id = str(uuid.uuid4())
    version_id = str(uuid.uuid4())
    now = utc_now()

    with db_session() as db:
        db.execute(
            """
            INSERT INTO reports (
                id, title, period_start, period_end, status, content,
                model_name, created_at, source_run_id
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report_id,
                payload.title,
                payload.period_start,
                payload.period_end,
                "Draft",
                content,
                settings.llm_model,
                now,
                source_run_id,
            ),
        )
        db.execute(
            """
            INSERT INTO report_versions (
                id, report_id, version_number, content, created_at
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (version_id, report_id, 1, content, now),
        )

    return GeneratedReport(
        id=report_id,
        title=payload.title,
        period_start=payload.period_start,
        period_end=payload.period_end,
        status="Draft",
        content=content,
        created_at=now,
        version_count=1,
    )


def generate_report(payload: GenerateReportRequest) -> GeneratedReport:
    invoice_summary = _format_invoice_summary(payload.period_start, payload.period_end)
    prompt = build_report_prompt(payload, invoice_summary)
    content = generate_report_content(prompt)
    validate_report_content(content)
    return save_report(payload, content)


def delete_report(report_id: str) -> bool:
    with db_session() as db:
        cursor = db.execute("DELETE FROM reports WHERE id = ?", (report_id,))
    return cursor.rowcount > 0


def list_reports() -> list[GeneratedReport]:
    with db_session() as db:
        rows = db.execute(
            """
            SELECT r.*, COUNT(v.id) AS version_count
            FROM reports r
            LEFT JOIN report_versions v ON v.report_id = r.id
            GROUP BY r.id
            ORDER BY r.created_at DESC
            """
        ).fetchall()

    return [
        GeneratedReport(
            id=row["id"],
            title=row["title"],
            period_start=row["period_start"],
            period_end=row["period_end"],
            status=row["status"],
            content=row["content"],
            created_at=row["created_at"],
            version_count=row["version_count"] or 1,
        )
        for row in rows
    ]


def get_report(report_id: str) -> GeneratedReport | None:
    with db_session() as db:
        row = db.execute(
            """
            SELECT r.*, COUNT(v.id) AS version_count
            FROM reports r
            LEFT JOIN report_versions v ON v.report_id = r.id
            WHERE r.id = ?
            GROUP BY r.id
            """,
            (report_id,),
        ).fetchone()

    if not row:
        return None

    return GeneratedReport(
        id=row["id"],
        title=row["title"],
        period_start=row["period_start"],
        period_end=row["period_end"],
        status=row["status"],
        content=row["content"],
        created_at=row["created_at"],
        version_count=row["version_count"] or 1,
    )


def build_report_pdf(report_id: str) -> bytes:
    report = get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    document = fitz.open()
    page_width = 595
    page_height = 842
    margin = 48
    footer_height = 32
    content_bottom = page_height - margin - footer_height
    page = document.new_page(width=page_width, height=page_height)
    y = margin

    def new_page() -> None:
        nonlocal page, y
        page = document.new_page(width=page_width, height=page_height)
        y = margin

    def ensure_space(height: float) -> None:
        if y + height > content_bottom:
            new_page()

    def write_wrapped(
        text: str,
        *,
        size: float = 10,
        color: tuple[float, float, float] = (0.15, 0.23, 0.34),
        font: str = "helv",
        indent: float = 0,
        gap_after: float = 6,
    ) -> None:
        nonlocal y
        text = re.sub(r"(\*\*|`)", "", text)
        width = page_width - (2 * margin) - indent
        line_height = size * 1.45
        max_chars = max(25, int(width / (size * 0.52)))
        words = text.split()
        lines: list[str] = []
        current = ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if len(candidate) > max_chars and current:
                lines.append(current)
                current = word
            else:
                current = candidate
        if current or not lines:
            lines.append(current)
        ensure_space((len(lines) * line_height) + gap_after)
        for line in lines:
            page.insert_text(
                (margin + indent, y),
                line,
                fontsize=size,
                fontname=font,
                color=color,
            )
            y += line_height
        y += gap_after

    def write_table(table_lines: list[str]) -> None:
        nonlocal y
        rows = [
            [cell.strip() for cell in line.strip().strip("|").split("|")]
            for line in table_lines
            if "---" not in line
        ]
        if not rows:
            return
        columns = max(len(row) for row in rows)
        cell_width = (page_width - (2 * margin)) / columns
        row_height = 34

        def draw_row(row: list[str], is_header: bool) -> None:
            nonlocal y
            fill = (0.90, 0.96, 0.94) if is_header else (1, 1, 1)
            for column_index in range(columns):
                value = row[column_index] if column_index < len(row) else ""
                value = re.sub(r"(\*\*|`)", "", value)
                rect = fitz.Rect(
                    margin + (column_index * cell_width),
                    y,
                    margin + ((column_index + 1) * cell_width),
                    y + row_height,
                )
                page.draw_rect(rect, color=(0.80, 0.85, 0.88), fill=fill, width=0.6)
                page.insert_textbox(
                    rect + (4, 4, -4, -4),
                    value,
                    fontsize=7.5,
                    fontname="helv",
                    color=(0.12, 0.20, 0.28),
                )
            y += row_height

        for row_index, row in enumerate(rows):
            if y + row_height > content_bottom:
                new_page()
                if row_index > 0:
                    draw_row(rows[0], is_header=True)
            draw_row(row, is_header=row_index == 0)
        y += 10

    page.draw_rect(
        fitz.Rect(0, 0, page_width, 210),
        color=(0.04, 0.35, 0.30),
        fill=(0.04, 0.35, 0.30),
    )
    page.insert_text(
        (margin, 74),
        "INVOICE-BASED ESG DISCLOSURE",
        fontsize=11,
        fontname="helv",
        color=(0.78, 0.94, 0.88),
    )
    page.insert_textbox(
        fitz.Rect(margin, 96, page_width - margin, 160),
        report.title,
        fontsize=24,
        fontname="helv",
        color=(1, 1, 1),
    )
    page.insert_text(
        (margin, 184),
        f"{report.period_start} to {report.period_end}  |  Status: {report.status}",
        fontsize=10,
        fontname="helv",
        color=(0.88, 0.96, 0.93),
    )
    y = 242
    write_wrapped(
        "This draft uses reviewed invoice expenditure and local ESG guideline "
        "evidence. It does not represent verified corporate ESG impact.",
        size=11,
        color=(0.35, 0.27, 0.08),
        gap_after=18,
    )

    lines = report.content.splitlines()
    index = 0
    while index < len(lines):
        raw = lines[index].strip()
        if not raw:
            y += 5
            index += 1
            continue
        if raw.startswith("|"):
            table_lines = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                table_lines.append(lines[index].strip())
                index += 1
            write_table(table_lines)
            continue
        if raw.startswith("#"):
            heading = raw.lstrip("#").strip()
            write_wrapped(
                heading,
                size=15,
                color=(0.04, 0.35, 0.30),
                font="helv",
                gap_after=10,
            )
        elif re.match(r"^\d+\.\s", raw):
            write_wrapped(
                raw,
                size=15,
                color=(0.04, 0.35, 0.30),
                font="helv",
                gap_after=10,
            )
        elif raw.startswith("- "):
            write_wrapped(f"- {raw[2:]}", indent=12)
        else:
            write_wrapped(raw)
        index += 1

    for page_index, pdf_page in enumerate(document, start=1):
        pdf_page.draw_line(
            (margin, page_height - 42),
            (page_width - margin, page_height - 42),
            color=(0.82, 0.86, 0.88),
            width=0.6,
        )
        pdf_page.insert_text(
            (margin, page_height - 25),
            "Invoice-Based ESG Draft Disclosure",
            fontsize=8,
            color=(0.40, 0.46, 0.52),
        )
        pdf_page.insert_text(
            (page_width - margin - 42, page_height - 25),
            f"Page {page_index}",
            fontsize=8,
            color=(0.40, 0.46, 0.52),
        )

    buffer = BytesIO()
    document.save(buffer)
    document.close()
    return buffer.getvalue()
