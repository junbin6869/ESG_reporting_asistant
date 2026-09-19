import re


REPORT_HEADINGS = [
    "1. Disclosure Overview and Reporting Boundary",
    "2. Performance Overview",
    "3. Environmental Performance",
    "4. Social Performance",
    "5. Governance Performance",
    "6. Executive Summary",
]

PERFORMANCE_TABLE_HEADER = (
    "| Category | Reviewed invoices | Reviewed invoice spend | "
    "Share of reviewed spend |"
)

INVOICE_BASED_DISCLOSURE = (
    "This is an invoice-based ESG draft disclosure. Reviewed invoice spend "
    "indicates expenditure signals and does not prove achieved ESG impact or performance."
)

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
- Each Environmental, Social, and Governance section must start with a concise
  category-level spend overview, followed by an "Observed Themes" subsection.
- Section 6 must provide a concise report-level summary of the reviewed invoice
  findings. It should summarise the ESG expenditure pattern and key observed
  themes from the preceding sections.
- Do not mention the agent, guideline-search limit, or retrieval-call count in
  the public report.
- Every observed theme must state its total reviewed spend, cite the supplied
  [INV-GRP:...] reference, and include the supplied guideline marker that explains
  the related disclosure topic.
- Immediately after every guideline-supported statement, append one or more
  internal markers in the form {{{{GUIDELINE:<chunk_id>}}}} using chunk IDs
  supplied in the available evidence. Statements that do not use guideline
  evidence do not need this marker.
- Do not write guideline Source, Topic, Section, Page, or a visible Chunk ID
  yourself. The system validates each marker and renders the public metadata.
- Use invoice citation tokens exactly as supplied in the reviewed invoice summary.
- Invoice groups represent specific observed themes, not whole ESG categories.
  Use the supplied [INV-GRP:...] token only for that theme's reviewed invoices.
- When discussing a specific invoice, cite its supplied [INV:...] token. Never
  invent, alter, or cite a token that was not supplied, and do not cite empty groups.
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
        "Observed Themes",
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
    ]
    for line in content.splitlines():
        if not line.strip().startswith("|"):
            continue
        normalized_line = _normalize_for_validation(line)
        if all(column in normalized_line for column in required_columns):
            return True
    return False
