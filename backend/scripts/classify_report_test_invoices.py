"""Classify seeded report-capacity invoices through the real RAG and LLM flow.

For every invoice from INV-2026-0011 to INV-2026-0100, the script retrieves
local ESG guideline evidence, calls the configured chat model, and saves the
returned category, reason, and evidence traceability as a reviewed result.
Completed records are skipped on later runs, so failures can be retried safely.
"""

from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.append(str(BACKEND_ROOT))

from app.db.database import db_session, init_db  # noqa: E402
from app.schemas import InvoiceClassification  # noqa: E402
from app.services.classification_service import classify_invoice  # noqa: E402
from app.services.invoice_service import save_classification  # noqa: E402


REVIEW_PROMPT_VERSION = "report_capacity_rag_review_v1"
FIRST_SEEDED_INVOICE = "INV-2026-0011"
LAST_SEEDED_INVOICE = "INV-2026-0100"


def invoices_to_classify(limit: int | None) -> list[tuple[str, str]]:
    with db_session() as db:
        rows = db.execute(
            """
            SELECT i.id, i.invoice_number
            FROM invoices i
            WHERE i.invoice_number BETWEEN ? AND ?
              AND COALESCE((
                  SELECT c.prompt_version
                  FROM invoice_classifications c
                  WHERE c.invoice_id = i.id AND c.invoice_version = i.version
                  ORDER BY c.created_at DESC, c.id DESC
                  LIMIT 1
              ), '') != ?
            ORDER BY i.invoice_number
            """,
            (FIRST_SEEDED_INVOICE, LAST_SEEDED_INVOICE, REVIEW_PROMPT_VERSION),
        ).fetchall()
    records = [(str(row["id"]), str(row["invoice_number"])) for row in rows]
    return records[:limit] if limit else records


def classify_and_review(invoice_id: str, invoice_number: str) -> tuple[str, int]:
    decision = classify_invoice(invoice_id)
    reviewed = InvoiceClassification(
        category=decision.category,
        status="Reviewed",
        confidence=decision.confidence,
        reason=decision.reason,
        evidence=decision.evidence,
    )
    save_classification(
        invoice_id=invoice_id,
        classification=reviewed,
        model_name="rag-llm-review",
        prompt_version=REVIEW_PROMPT_VERSION,
    )
    return invoice_number, len(decision.evidence)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    init_db()
    records = invoices_to_classify(args.limit)
    if not records:
        print("All seeded invoices already have real RAG and LLM classifications.")
        return

    workers = max(1, min(args.workers, 8))
    print(f"Classifying {len(records)} seeded invoices with {workers} worker(s).")
    completed = 0
    failed = 0
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(classify_and_review, invoice_id, invoice_number): invoice_number
            for invoice_id, invoice_number in records
        }
        for future in as_completed(futures):
            invoice_number = futures[future]
            try:
                completed_invoice, evidence_count = future.result()
                completed += 1
                print(
                    f"Completed {completed}/{len(records)}: {completed_invoice} "
                    f"({evidence_count} evidence item(s))."
                )
            except Exception as exc:
                failed += 1
                print(f"Failed {invoice_number}: {exc}", file=sys.stderr)

    print(f"Finished: {completed} completed, {failed} failed.")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
