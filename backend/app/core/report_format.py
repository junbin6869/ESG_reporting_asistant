import re


REPORT_HEADINGS = [
    "1. Disclosure Overview and Reporting Boundary",
    "2. Performance Overview",
    "3. Environmental Performance",
    "4. Social Performance",
    "5. Governance Performance",
    "6. Notable ESG Themes and Expenditure Narratives",
    "7. Guideline Alignment and Traceability",
    "8. Data Quality, Limitations and Missing KPIs",
    "9. Forward Actions and Human Review",
]

PERFORMANCE_TABLE_HEADER = (
    "| Category | Reviewed invoices | Reviewed invoice spend | "
    "Share of reviewed spend | Evidence coverage |"
)

INVOICE_BASED_DISCLOSURE = (
    "This is an invoice-based ESG draft disclosure. Reviewed invoice spend "
    "indicates expenditure signals and does not prove achieved ESG impact or performance."
)

MISSING_KPI_TEXT = "Not available from invoice evidence"


def report_contract() -> str:
    headings = "\n".join(REPORT_HEADINGS)
    return f"""
Write the report as Markdown and use exactly these numbered section headings:
{headings}

Mandatory reporting rules:
- State this disclosure sentence verbatim in section 1:
  "{INVOICE_BASED_DISCLOSURE}"
- Section 2 must include this exact Markdown table header:
  {PERFORMANCE_TABLE_HEADER}
- Report reviewed invoice count and spend as expenditure signals only.
- Never describe invoice spend as achieved ESG impact, emissions reduction,
  energy savings, safety improvement, community benefit, or governance outcome.
- Treat Non-ESG invoices as excluded boundary items, not ESG performance.
- Each Environmental, Social, and Governance section must cover observed invoice
  themes, reviewed spend signals, guideline alignment, and missing outcome metrics.
- For every unavailable impact KPI, write "{MISSING_KPI_TEXT}".
- Guideline references must include source, section, page, and chunk ID.
- Section 9 must separate Evidence collection, Operational actions, Governance
  actions, and Human review.
- Use concise paragraphs, Markdown tables, and hyphen bullet lists.
""".strip()


def validate_report_content(content: str) -> None:
    normalized_content = _normalize_for_validation(content)
    missing = [
        heading
        for heading in REPORT_HEADINGS
        if _normalize_for_validation(heading) not in normalized_content
    ]
    if missing:
        raise ValueError(
            "Agent report is missing required section headings: " + ", ".join(missing)
        )

    required_markers = [
        INVOICE_BASED_DISCLOSURE,
        MISSING_KPI_TEXT,
        "Evidence collection",
        "Operational actions",
        "Governance actions",
        "Human review",
    ]
    missing_markers = [
        marker
        for marker in required_markers
        if _normalize_for_validation(marker) not in normalized_content
    ]
    if not _has_performance_table_header(content):
        missing_markers.insert(0, PERFORMANCE_TABLE_HEADER)
    if missing_markers:
        raise ValueError(
            "Agent report is missing required disclosure content: "
            + ", ".join(missing_markers)
        )


def _normalize_for_validation(value: str) -> str:
    without_markdown = re.sub(r"[#*`_>|-]", " ", value)
    return re.sub(r"\s+", " ", without_markdown).strip().casefold()


def _has_performance_table_header(content: str) -> bool:
    required_columns = [
        "category",
        "reviewed invoices",
        "reviewed invoice spend",
        "share of reviewed spend",
        "evidence coverage",
    ]
    for line in content.splitlines():
        if not line.strip().startswith("|"):
            continue
        normalized_line = _normalize_for_validation(line)
        if all(column in normalized_line for column in required_columns):
            return True
    return False
