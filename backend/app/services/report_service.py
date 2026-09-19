import csv
import re
import uuid
from collections import defaultdict
from dataclasses import dataclass
from io import BytesIO, StringIO
from typing import Any
from zipfile import ZIP_DEFLATED, ZipFile

from fastapi import HTTPException
import fitz
from langchain_core.messages import HumanMessage, SystemMessage

from app.core.config import settings
from app.core.llm import get_chat_model
from app.core.report_format import REPORT_HEADINGS, report_contract, validate_report_content
from app.db.database import db_session, utc_now
from app.schemas import EvidenceItem, GenerateReportRequest, GeneratedReport, InvoiceRow
from app.services.invoice_service import list_invoices
from app.services.rag_service import (
    document_to_evidence,
    get_guideline_document_by_id,
)


THEME_GROUP_PREFIXES = {
    "Environmental": "E",
    "Social": "S",
    "Governance": "G",
    "Non-ESG": "N",
}
PERFORMANCE_CATEGORIES = ("Environmental", "Social", "Governance")
THEME_RULES: dict[str, list[tuple[str, tuple[str, ...], tuple[str, ...]]]] = {
    "Environmental": [
        ("Waste collection and recycling services", ("waste", "recycling"), ("Total waste generated", "Waste recycled or recovered")),
        ("Energy consumption and renewable energy", ("energy", "solar", "led", "electric", "renewable", "charging"), ("Total energy consumed", "Renewable energy share")),
        ("Water supply and efficiency audits", ("water", "rainwater", "leak"), ("Total volume of water used", "Water efficiency improvement")),
        ("Environmental management practices", ("emission", "carbon", "air", "packaging", "tree", "environment"), ("Greenhouse-gas emissions", "Environmental compliance outcome")),
    ],
    "Social": [
        ("Health, safety and employee wellbeing", ("safety", "health", "medical", "wellbeing", "mental", "ergonomic", "first-aid", "ppe"), ("Work-related fatalities", "Lost time incident rate", "Employees trained on health and safety standards")),
        ("Workforce learning and development", ("training", "upskilling", "apprentice", "mentoring", "development"), ("Training hours per employee", "Training completion rate")),
        ("Diversity, inclusion and employee support", ("diversity", "inclusion", "childcare", "family"), ("Workforce diversity metrics", "Employee support programme outcomes")),
        ("Community investment and education", ("community", "stem", "school"), ("Community beneficiaries", "Community programme outcomes")),
    ],
    "Governance": [
        ("Data protection and cybersecurity", ("data", "cyber", "information"), ("Data breaches", "Cybersecurity incident rate")),
        ("Ethics and stakeholder accountability", ("bribery", "whistle", "ethical", "supplier code"), ("Substantiated misconduct cases", "Whistleblowing cases resolved")),
        ("Internal controls and corporate governance", ("audit", "control", "board", "risk"), ("Control deficiencies remediated", "Board governance outcomes")),
        ("Regulatory, legal and tax compliance", ("regulatory", "legal", "tax", "records"), ("Material compliance breaches", "Regulatory actions or penalties")),
        ("ESG assurance and disclosure governance", ("esg", "assurance", "disclosure"), ("Disclosure assurance findings", "ESG governance review outcomes")),
    ],
}


@dataclass
class InvoiceThemeGroup:
    group_id: str
    name: str
    category: str
    invoices: list[InvoiceRow]
    missing_metrics: tuple[str, ...]
    evidence: EvidenceItem | None

_BRACKETED_REFERENCE_PATTERN = re.compile(r"\[([^\[\]\r\n]+)\]")
_INVOICE_REFERENCE_PREFIX_PATTERN = re.compile(
    r"(?i)\bINV(?:-GRP)?\s*:"
)
_GUIDELINE_MARKER_PATTERN = re.compile(r"\{\{GUIDELINE:([^{}\r\n]+)\}\}")
_GUIDELINE_MARKER_PREFIX_PATTERN = re.compile(r"(?i)\{\{\s*GUIDELINE")
_CHUNK_ID_LABEL_PATTERN = re.compile(r"(?i)\bchunk[\s_-]*id\b")
_GUIDELINE_METADATA_LABEL_PATTERNS = {
    "source": re.compile(r"(?i)\bsource\s*[:=]"),
    "section": re.compile(r"(?i)\bsection\s*[:=]"),
    "page": re.compile(r"(?i)\bpage\s*[:=]"),
}


def _reviewed_invoices_for_period(period_start: str, period_end: str) -> list[InvoiceRow]:
    return [
        invoice
        for invoice in list_invoices()
        if period_start <= invoice.invoice_date <= period_end
        and invoice.classification
        and invoice.classification.status == "Reviewed"
    ]


def _format_invoice_summary(period_start: str, period_end: str) -> str:
    return build_reviewed_invoice_summary(
        _reviewed_invoices_for_period(period_start, period_end)
    )


def invoice_citation_token(invoice_number: str) -> str:
    """Return the stable report citation token for an invoice snapshot."""
    return f"INV:{invoice_number}"


def build_invoice_citation_register(
    invoices: list[InvoiceRow],
) -> tuple[set[str], set[str]]:
    """Return allowed citations and required performance-theme citations."""
    reviewed_invoices = [
        invoice
        for invoice in invoices
        if invoice.classification and invoice.classification.status == "Reviewed"
    ]
    allowed = {
        invoice_citation_token(invoice.invoice_number)
        for invoice in reviewed_invoices
    }
    groups = build_invoice_theme_groups(reviewed_invoices)
    required_groups = {
        group.group_id for group in groups if group.category in PERFORMANCE_CATEGORIES
    }
    allowed.update(required_groups)
    return allowed, required_groups


def extract_report_invoice_citations(content: str) -> set[str]:
    """Extract well-formed, bracketed invoice citations from report Markdown."""
    citations, _format_issues = _parse_report_invoice_citations(content)
    return citations


def validate_report_invoice_citations(
    content: str,
    invoices: list[InvoiceRow],
    *,
    period_start: str | None = None,
    period_end: str | None = None,
) -> set[str]:
    """Validate report citations against one immutable reviewed-invoice snapshot.

    The caller supplies the exact snapshot used in the LLM prompt. Reusing that
    snapshot for the Evidence Register makes invoice version, review status, and
    reporting-period eligibility auditable without asking an LLM to self-check.
    """
    snapshot_issues: list[str] = []
    for invoice in invoices:
        if not invoice.classification or invoice.classification.status != "Reviewed":
            snapshot_issues.append(
                f"{invoice.invoice_number} is not a Reviewed invoice"
            )
        if period_start and invoice.invoice_date < period_start:
            snapshot_issues.append(
                f"{invoice.invoice_number} is before the reporting period"
            )
        if period_end and invoice.invoice_date > period_end:
            snapshot_issues.append(
                f"{invoice.invoice_number} is after the reporting period"
            )
        if invoice.version < 1:
            snapshot_issues.append(
                f"{invoice.invoice_number} has an invalid invoice version"
            )
    if snapshot_issues:
        raise ValueError(
            "Invoice citation snapshot is invalid: " + "; ".join(snapshot_issues)
        )

    allowed, required_groups = build_invoice_citation_register(invoices)
    citations, format_issues = _parse_report_invoice_citations(content)
    unknown = sorted(citations - allowed)
    missing_groups = sorted(required_groups - citations)

    issues: list[str] = []
    if format_issues:
        issues.append(
            "malformed citation(s): " + ", ".join(sorted(set(format_issues)))
        )
    if unknown:
        issues.append("unknown or ineligible citation(s): " + ", ".join(unknown))
    if missing_groups:
        issues.append(
            "missing required theme citation(s): " + ", ".join(missing_groups)
        )
    if issues:
        raise ValueError("Invoice citation validation failed: " + "; ".join(issues))
    return citations


def _parse_report_invoice_citations(content: str) -> tuple[set[str], list[str]]:
    citations: set[str] = set()
    format_issues: list[str] = []
    invoice_reference_spans: list[tuple[int, int]] = []

    for match in _BRACKETED_REFERENCE_PATTERN.finditer(content):
        inner = match.group(1)
        stripped = inner.strip()
        if not _INVOICE_REFERENCE_PREFIX_PATTERN.match(stripped):
            continue
        invoice_reference_spans.append(match.span())

        if stripped != inner:
            format_issues.append(f"[{inner}]")
            continue
        if stripped.startswith("INV-GRP:"):
            if not re.fullmatch(r"INV-GRP:[ESGN]-\d{2}", stripped):
                format_issues.append(f"[{inner}]")
                continue
        elif stripped.startswith("INV:"):
            if not stripped.removeprefix("INV:").strip():
                format_issues.append(f"[{inner}]")
                continue
        else:
            format_issues.append(f"[{inner}]")
            continue
        citations.add(stripped)

    for match in _INVOICE_REFERENCE_PREFIX_PATTERN.finditer(content):
        if any(start <= match.start() < end for start, end in invoice_reference_spans):
            continue
        format_issues.append(f"{match.group(0)} (must be enclosed in square brackets)")

    return citations, format_issues


def build_invoice_guideline_chunk_ids(invoices: list[InvoiceRow]) -> set[str]:
    """Collect guideline chunks already attached to the invoice snapshot."""
    return {
        evidence.chunk_id
        for invoice in invoices
        if invoice.classification
        for evidence in invoice.classification.evidence
        if evidence.chunk_id.strip()
    }


def validate_and_render_guideline_citations(
    content: str,
    allowed_chunk_ids: set[str],
) -> tuple[str, list[dict[str, Any]]]:
    """Validate internal Markdown markers and render public guideline metadata."""
    allowed = {chunk_id.strip() for chunk_id in allowed_chunk_ids if chunk_id.strip()}
    matches = list(_GUIDELINE_MARKER_PATTERN.finditer(content))
    content_without_markers = _GUIDELINE_MARKER_PATTERN.sub("", content)
    issues: list[str] = []

    if _GUIDELINE_MARKER_PREFIX_PATTERN.search(content_without_markers):
        issues.append("malformed {{GUIDELINE:<chunk_id>}} marker")
    if _CHUNK_ID_LABEL_PATTERN.search(content_without_markers):
        issues.append("visible Chunk ID text is not allowed")
    if _contains_manual_guideline_metadata(content_without_markers):
        issues.append(
            "guideline Source, Section, and Page must be rendered by the system"
        )

    marker_ids: list[str] = []
    for match in matches:
        raw_chunk_id = match.group(1)
        chunk_id = raw_chunk_id.strip()
        if raw_chunk_id != chunk_id or not chunk_id:
            issues.append(f"malformed guideline marker: {match.group(0)}")
            continue
        marker_ids.append(chunk_id)

    unknown = sorted(set(marker_ids) - allowed)
    if unknown:
        issues.append(
            "guideline marker(s) were not supplied to this report run: "
            + ", ".join(unknown)
        )

    leaked_known_ids = sorted(
        chunk_id for chunk_id in allowed if chunk_id in content_without_markers
    )
    if leaked_known_ids:
        issues.append(
            "guideline Chunk ID(s) must only appear inside internal markers: "
            + ", ".join(leaked_known_ids)
        )
    if issues:
        raise ValueError("Guideline citation validation failed: " + "; ".join(issues))

    lookup_cache: dict[str, dict[str, Any]] = {}
    audit_records: list[dict[str, Any]] = []

    def render_marker(match: re.Match[str]) -> str:
        chunk_id = match.group(1).strip()
        metadata = lookup_cache.get(chunk_id)
        if metadata is None:
            document = get_guideline_document_by_id(chunk_id)
            if document is None:
                raise ValueError(
                    "Guideline citation validation failed: exact Chunk ID was not "
                    f"found in Chroma: {chunk_id}"
                )

            actual_chunk_id = str(
                document.metadata.get("chunk_id") or document.id or ""
            )
            if actual_chunk_id != chunk_id:
                raise ValueError(
                    "Guideline citation validation failed: Chroma returned a "
                    f"different Chunk ID for {chunk_id}: {actual_chunk_id or 'missing'}"
                )

            evidence = document_to_evidence(document)
            metadata = {
                "chunk_id": chunk_id,
                "source": _citation_display_value(evidence.source),
                "topic": _citation_display_value(evidence.topic),
                "section": _citation_display_value(evidence.section),
                "page": _citation_display_value(evidence.page),
                "source_version": str(
                    document.metadata.get("source_version") or ""
                ),
                "index_version": str(document.metadata.get("index_version") or ""),
                "supporting_text": document.page_content.strip(),
            }
            missing_metadata = [
                field
                for field in ["source", "topic", "section", "page", "supporting_text"]
                if not metadata[field]
            ]
            if missing_metadata:
                raise ValueError(
                    "Guideline citation validation failed: Chroma record "
                    f"{chunk_id} is missing " + ", ".join(missing_metadata)
                )
            lookup_cache[chunk_id] = metadata

        audit_records.append(
            {
                **metadata,
                "occurrence_index": len(audit_records) + 1,
                "claim_text": _claim_before_marker(content, match.start()),
                "validation_status": "Passed",
            }
        )
        return (
            f"(Source: {metadata['source']}; Topic: {metadata['topic']}; "
            f"Section: {metadata['section']}; Page: {metadata['page']})"
        )

    rendered_content = _GUIDELINE_MARKER_PATTERN.sub(render_marker, content)
    if _GUIDELINE_MARKER_PREFIX_PATTERN.search(rendered_content):
        raise ValueError(
            "Guideline citation validation failed: an unresolved guideline marker "
            "remains in the public report"
        )
    return rendered_content, audit_records


def _contains_manual_guideline_metadata(content: str) -> bool:
    for line in content.splitlines():
        if all(pattern.search(line) for pattern in _GUIDELINE_METADATA_LABEL_PATTERNS.values()):
            return True
    return False


def _citation_display_value(value: object | None) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text.replace("|", r"\|").replace("{{", "").replace("}}", "")


def _claim_before_marker(content: str, marker_start: int) -> str:
    prefix = content[:marker_start].rstrip()
    if not prefix:
        return ""
    paragraphs = re.split(r"\n\s*\n", prefix)
    return paragraphs[-1].strip()[-2000:]


def build_invoice_theme_groups(invoices: list[InvoiceRow]) -> list[InvoiceThemeGroup]:
    """Group reviewed invoices into traceable, category-specific ESG themes."""
    reviewed = [
        invoice
        for invoice in invoices
        if invoice.classification and invoice.classification.status == "Reviewed"
    ]
    groups: list[InvoiceThemeGroup] = []
    for category in [*PERFORMANCE_CATEGORIES, "Non-ESG"]:
        category_invoices = [
            invoice
            for invoice in reviewed
            if invoice.classification and invoice.classification.category == category
        ]
        buckets: dict[str, list[InvoiceRow]] = defaultdict(list)
        metrics_by_theme: dict[str, tuple[str, ...]] = {}
        for invoice in category_invoices:
            name, metrics = _theme_for_invoice(invoice, category)
            buckets[name].append(invoice)
            metrics_by_theme[name] = metrics

        prefix = THEME_GROUP_PREFIXES[category]
        for index, name in enumerate(
            sorted(buckets, key=lambda item: _theme_sort_key(category, item)), start=1
        ):
            grouped_invoices = sorted(
                buckets[name], key=lambda item: (item.invoice_date, item.invoice_number)
            )
            groups.append(
                InvoiceThemeGroup(
                    group_id=f"INV-GRP:{prefix}-{index:02d}",
                    name=name,
                    category=category,
                    invoices=grouped_invoices,
                    missing_metrics=metrics_by_theme[name],
                    evidence=_first_group_evidence(grouped_invoices),
                )
            )
    return groups


def _theme_for_invoice(invoice: InvoiceRow, category: str) -> tuple[str, tuple[str, ...]]:
    description = invoice.description.casefold()
    for name, keywords, metrics in THEME_RULES.get(category, []):
        if any(keyword in description for keyword in keywords):
            return name, metrics

    classification = invoice.classification
    if classification and classification.evidence:
        topic = classification.evidence[0].topic.strip()
        if topic:
            return topic, (f"Outcome metric for {topic}",)
    if category == "Non-ESG":
        return "Non-ESG boundary items", ("Non-ESG outcome metric",)
    return f"Other {category.lower()} expenditure", (
        f"Outcome metric for {category.lower()} expenditure",
    )


def _theme_sort_key(category: str, name: str) -> tuple[int, str]:
    ordered_names = [rule[0] for rule in THEME_RULES.get(category, [])]
    try:
        return ordered_names.index(name), name
    except ValueError:
        return len(ordered_names), name


def _first_group_evidence(invoices: list[InvoiceRow]) -> EvidenceItem | None:
    for invoice in invoices:
        if invoice.classification and invoice.classification.evidence:
            return invoice.classification.evidence[0]
    return None


def _group_spend_by_currency(group: InvoiceThemeGroup) -> dict[str, float]:
    spend: dict[str, float] = defaultdict(float)
    for invoice in group.invoices:
        spend[invoice.currency] += invoice.amount
    return dict(spend)


def _format_spend(spend_by_currency: dict[str, float]) -> str:
    return "; ".join(
        f"{currency} {amount:.2f}"
        for currency, amount in sorted(spend_by_currency.items())
    ) or "none"


def build_reviewed_invoice_summary(invoices: list[InvoiceRow]) -> str:
    lines: list[str] = []
    totals_by_currency: dict[str, float] = defaultdict(float)
    for invoice in invoices:
        totals_by_currency[invoice.currency] += invoice.amount

    theme_groups = build_invoice_theme_groups(invoices)
    for category in [*PERFORMANCE_CATEGORIES, "Non-ESG"]:
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
        for group in (item for item in theme_groups if item.category == category):
            lines.append(
                f"- Theme group [{group.group_id}]: {group.name}; "
                f"reviewed spend={_format_spend(_group_spend_by_currency(group))}; "
                f"invoice count={len(group.invoices)}; "
                f"missing metrics={'; '.join(group.missing_metrics)}; "
                f"guideline evidence="
                + (
                    f"marker={{{{GUIDELINE:{group.evidence.chunk_id}}}}}; "
                    f"source={group.evidence.source}; topic={group.evidence.topic or 'not specified'}; "
                    f"section={group.evidence.section or 'not specified'}; "
                    f"page={group.evidence.page or 'not specified'}"
                    if group.evidence
                    else "none"
                )
            )
            for invoice in group.invoices:
                lines.append(
                    f"  - [{invoice_citation_token(invoice.invoice_number)}] "
                    f"{invoice.invoice_number}: {invoice.description}; "
                    f"spend={invoice.currency} {invoice.amount:.2f}"
                )
        lines.append("")
    return "\n".join(lines).strip()


def render_theme_performance_sections(content: str, invoices: list[InvoiceRow]) -> str:
    """Render traceable Section 3–5 content from the immutable invoice snapshot."""
    groups = build_invoice_theme_groups(invoices)
    total_by_currency: dict[str, float] = defaultdict(float)
    for invoice in invoices:
        total_by_currency[invoice.currency] += invoice.amount

    rendered = content
    section_headings = {
        "Environmental": REPORT_HEADINGS[2],
        "Social": REPORT_HEADINGS[3],
        "Governance": REPORT_HEADINGS[4],
    }
    for category, heading in section_headings.items():
        body = _render_theme_performance_body(
            category,
            [group for group in groups if group.category == category],
            dict(total_by_currency),
        )
        rendered = _replace_report_section(rendered, heading, body)
    return rendered


def _render_theme_performance_body(
    category: str,
    groups: list[InvoiceThemeGroup],
    total_by_currency: dict[str, float],
) -> str:
    category_spend: dict[str, float] = defaultdict(float)
    for group in groups:
        for currency, amount in _group_spend_by_currency(group).items():
            category_spend[currency] += amount

    spend_overview = []
    for currency, amount in sorted(category_spend.items()):
        all_reviewed_spend = total_by_currency.get(currency, 0)
        share = amount / all_reviewed_spend * 100 if all_reviewed_spend else 0
        spend_overview.append(
            f"{currency} {amount:.2f}, representing {share:.1f}% of the total "
            f"reviewed {currency} spend"
        )
    overview = "; ".join(spend_overview) or "no reviewed spend"
    lines = [
        (
            f"The reviewed invoices in the {category} category primarily relate to "
            f"{_theme_names_sentence(groups)}. The total reviewed spend in this "
            f"category is {overview}."
        ),
        "",
        "### Observed Themes",
    ]
    for group in groups:
        lines.append(f"- **{group.name}**")
        lines.append(
            f"  - **Total reviewed spend:** "
            f"{_format_spend(_group_spend_by_currency(group))} "
            f"· **Reference:** [{group.group_id}]"
        )
        if group.evidence:
            lines.append(
                f"  - **Evidence:** {{{{GUIDELINE:{group.evidence.chunk_id}}}}}"
            )
        else:
            lines.append("  - **Evidence:** No linked guideline evidence.")

    return "\n".join(lines)


def _theme_names_sentence(groups: list[InvoiceThemeGroup]) -> str:
    names = [group.name.lower() for group in groups]
    if not names:
        return "no reviewed invoice themes"
    if len(names) == 1:
        return names[0]
    if len(names) == 2:
        return " and ".join(names)
    return ", ".join(names[:-1]) + f", and {names[-1]}"


def _replace_report_section(content: str, heading: str, body: str) -> str:
    heading_pattern = re.compile(
        rf"(?m)^(?:\s*#+\s*)?{re.escape(heading)}\s*$"
    )
    current = heading_pattern.search(content)
    if not current:
        raise ValueError(f"Report is missing required section heading: {heading}")

    next_index = REPORT_HEADINGS.index(heading) + 1
    next_heading = REPORT_HEADINGS[next_index] if next_index < len(REPORT_HEADINGS) else None
    following = (
        re.compile(rf"(?m)^(?:\s*#+\s*)?{re.escape(next_heading)}\s*$").search(
            content,
            current.end(),
        )
        if next_heading
        else None
    )
    section_end = following.start() if following else len(content)
    return f"{content[:current.end()]}\n\n{body}\n\n{content[section_end:]}"


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
    evidence_register_invoices: list[InvoiceRow] | None = None,
    guideline_citations: list[dict[str, Any]] | None = None,
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
    if evidence_register_invoices is None:
        evidence_register_invoices = _reviewed_invoices_for_period(
            payload.period_start,
            payload.period_end,
        )

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
        _save_evidence_register_snapshot(
            db,
            report_id=report_id,
            invoices=evidence_register_invoices,
            created_at=now,
        )
        _save_guideline_citation_audit(
            db,
            report_id=report_id,
            citations=guideline_citations or [],
            created_at=now,
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


def _save_evidence_register_snapshot(
    db,
    *,
    report_id: str,
    invoices: list[InvoiceRow],
    created_at: str,
) -> None:
    """Persist one invoice row under its report-time thematic evidence group."""
    for group in build_invoice_theme_groups(invoices):
        evidence = group.evidence
        for invoice in group.invoices:
            classification = invoice.classification
            if not classification:
                continue
            db.execute(
                """
                INSERT INTO report_evidence_register_items (
                    id, report_id, group_id, group_name, invoice_id, invoice_number,
                    invoice_version, supplier_name, invoice_date, description, amount,
                    currency, classification_category, classification_status,
                    classification_reason, guideline_source, guideline_section,
                    guideline_topic, guideline_page, guideline_chunk_id, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    report_id,
                    group.group_id,
                    group.name,
                    invoice.id,
                    invoice.invoice_number,
                    invoice.version,
                    invoice.supplier_name,
                    invoice.invoice_date,
                    invoice.description,
                    invoice.amount,
                    invoice.currency,
                    classification.category,
                    classification.status,
                    classification.reason,
                    evidence.source if evidence else None,
                    evidence.section if evidence else None,
                    evidence.topic if evidence else None,
                    str(evidence.page) if evidence and evidence.page is not None else None,
                    evidence.chunk_id if evidence else None,
                    created_at,
                ),
            )


def _save_guideline_citation_audit(
    db,
    *,
    report_id: str,
    citations: list[dict[str, Any]],
    created_at: str,
) -> None:
    """Persist technical guideline IDs separately from the public report."""
    for citation in citations:
        db.execute(
            """
            INSERT INTO report_guideline_citations (
                id, report_id, occurrence_index, claim_text, chunk_id, source,
                topic, section, page, source_version, index_version,
                supporting_text, validation_status, created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(uuid.uuid4()),
                report_id,
                int(citation["occurrence_index"]),
                str(citation.get("claim_text") or ""),
                str(citation["chunk_id"]),
                str(citation["source"]),
                str(citation["topic"]),
                str(citation["section"]),
                str(citation["page"]),
                str(citation.get("source_version") or ""),
                str(citation.get("index_version") or ""),
                str(citation.get("supporting_text") or ""),
                str(citation.get("validation_status") or "Passed"),
                created_at,
            ),
        )


def build_report_evidence_register_csv(report_id: str) -> bytes:
    """Build the separate invoice-level Evidence Register for one saved report."""
    report = get_report(report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Report not found")

    with db_session() as db:
        rows = db.execute(
            """
            SELECT group_id, group_name, invoice_id, invoice_number, invoice_version,
                   supplier_name, invoice_date, description, amount, currency,
                   classification_category, classification_status,
                   classification_reason, guideline_source, guideline_section,
                   guideline_topic, guideline_page
            FROM report_evidence_register_items
            WHERE report_id = ?
            ORDER BY group_id, invoice_date, invoice_number, guideline_chunk_id
            """,
            (report_id,),
        ).fetchall()

    output = StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(
        [
            "Invoice Group",
            "Invoice Number",
            "Invoice Description",
            "Amount",
            "Date",
            "Classification",
        ]
    )
    for row in rows:
        values = [
            f"[{row['group_id']}] {row['group_name']}",
            row["invoice_number"],
            row["description"],
            f"{row['currency']} {row['amount']:.2f}",
            row["invoice_date"],
            row["classification_category"],
        ]
        writer.writerow([_safe_csv_cell(value) for value in values])

    return output.getvalue().encode("utf-8-sig")


def build_report_package(report_id: str) -> bytes:
    """Package a report PDF and its immutable CSV appendix into one ZIP file."""
    if not get_report(report_id):
        raise HTTPException(status_code=404, detail="Report not found")

    archive = BytesIO()
    with ZipFile(archive, mode="w", compression=ZIP_DEFLATED) as zip_file:
        zip_file.writestr(f"esg-report-{report_id}.pdf", build_report_pdf(report_id))
        zip_file.writestr(
            f"reference-register-{report_id}.csv",
            build_report_evidence_register_csv(report_id),
        )
    return archive.getvalue()


def _safe_csv_cell(value: object | None) -> str:
    """Prevent spreadsheet applications from treating evidence text as a formula."""
    text = "" if value is None else str(value)
    return f"'{text}" if text.startswith(("=", "+", "-", "@")) else text


def generate_report(payload: GenerateReportRequest) -> GeneratedReport:
    invoices = _reviewed_invoices_for_period(payload.period_start, payload.period_end)
    invoice_summary = build_reviewed_invoice_summary(invoices)
    prompt = build_report_prompt(payload, invoice_summary)
    content = generate_report_content(prompt)
    content = render_theme_performance_sections(content, invoices)
    validate_report_content(content)
    validate_report_invoice_citations(
        content,
        invoices,
        period_start=payload.period_start,
        period_end=payload.period_end,
    )
    content, guideline_citations = validate_and_render_guideline_citations(
        content,
        build_invoice_guideline_chunk_ids(invoices),
    )
    return save_report(
        payload,
        content,
        evidence_register_invoices=invoices,
        guideline_citations=guideline_citations,
    )


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

    def write_bullet(text: str, *, level: int) -> None:
        """Render a stable PDF bullet without relying on unsupported Unicode glyphs."""
        nonlocal y
        text = re.sub(r"(\*\*|`)", "", text)
        marker_x = margin + 12 + (level * 18)
        text_x = marker_x + 10
        size = 10
        line_height = size * 1.45
        width = page_width - margin - text_x
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

        ensure_space((len(lines) * line_height) + 4)
        marker_center = (marker_x + 2.5, y - (size * 0.32))
        marker_color = (0.15, 0.23, 0.34) if level == 0 else (0.30, 0.36, 0.45)
        page.draw_circle(
            marker_center,
            2.2 if level == 0 else 2.0,
            color=marker_color,
            fill=marker_color if level == 0 else None,
            width=0.8,
        )
        for line in lines:
            page.insert_text(
                (text_x, y),
                line,
                fontsize=size,
                fontname="helv",
                color=marker_color,
            )
            y += line_height
        y += 4

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
        source_line = lines[index]
        raw = source_line.strip()
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
        else:
            bullet_match = re.match(r"^(\s*)-\s+(.*)$", source_line)
            if bullet_match:
                leading_whitespace, bullet_text = bullet_match.groups()
                level = 1 if leading_whitespace else 0
                write_bullet(bullet_text, level=level)
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
