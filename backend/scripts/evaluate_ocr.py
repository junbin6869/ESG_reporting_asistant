"""Evaluate invoice PDF extraction and OCR against fixed ground-truth fields."""

from __future__ import annotations

import csv
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
GROUND_TRUTH_PATH = BACKEND_ROOT / "evaluation" / "ocr_ground_truth.json"
RESULTS_DIR = BACKEND_ROOT / "evaluation" / "results"
FIELDS = ["invoice_number", "invoice_date", "supplier_name", "amount", "description"]
sys.path.append(str(BACKEND_ROOT))

from app.services.invoice_extraction_service import _preview_invoice_content  # noqa: E402


def normalize_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold()


def field_matches(field: str, expected: object, actual: object) -> bool:
    if field == "amount":
        try:
            return abs(float(expected) - float(actual)) < 0.01
        except (TypeError, ValueError):
            return False
    return normalize_text(expected) == normalize_text(actual)


def main() -> None:
    if not GROUND_TRUTH_PATH.exists():
        raise SystemExit(
            "OCR ground truth not found. Run: python scripts/build_ocr_test_pdfs.py"
        )
    records = json.loads(GROUND_TRUTH_PATH.read_text(encoding="utf-8"))
    rows: list[dict] = []

    print(f"Evaluating {len(records)} invoice PDFs...")
    for index, record in enumerate(records, start=1):
        path = PROJECT_ROOT / record["file"]
        started = time.perf_counter()
        preview = _preview_invoice_content(path.read_bytes(), path.name)
        elapsed = time.perf_counter() - started
        actual = preview.invoice.model_dump()
        comparisons = {
            field: field_matches(field, record[field], actual.get(field))
            for field in FIELDS
        }
        row = {
            "file": record["file"],
            "condition": record["condition"],
            "method": preview.method,
            "elapsed_seconds": round(elapsed, 4),
            "all_fields_correct": all(comparisons.values()),
            **{f"{field}_correct": comparisons[field] for field in FIELDS},
            **{f"expected_{field}": record[field] for field in FIELDS},
            **{f"actual_{field}": actual.get(field) for field in FIELDS},
            "warnings": " | ".join(preview.warnings),
        }
        rows.append(row)
        status = "PASS" if row["all_fields_correct"] else "PARTIAL"
        correct_fields = sum(comparisons.values())
        print(
            f"[{index:02d}/{len(records):02d}] {status:<7} {path.name} "
            f"({correct_fields}/{len(FIELDS)} fields, {elapsed:.2f}s, {preview.method})"
        )

    total_fields = len(rows) * len(FIELDS)
    correct_fields = sum(
        row[f"{field}_correct"] for row in rows for field in FIELDS
    )
    metrics = {
        "document_count": len(rows),
        "documents_all_fields_correct": sum(row["all_fields_correct"] for row in rows),
        "document_success_rate": sum(row["all_fields_correct"] for row in rows) / len(rows),
        "overall_field_accuracy": correct_fields / total_fields,
        "average_latency_seconds": sum(row["elapsed_seconds"] for row in rows) / len(rows),
        "field_accuracy": {
            field: sum(row[f"{field}_correct"] for row in rows) / len(rows)
            for field in FIELDS
        },
        "condition_metrics": {},
    }
    by_condition: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_condition[row["condition"]].append(row)
    for condition, condition_rows in by_condition.items():
        condition_correct = sum(
            row[f"{field}_correct"]
            for row in condition_rows
            for field in FIELDS
        )
        metrics["condition_metrics"][condition] = {
            "documents": len(condition_rows),
            "document_success_rate": sum(
                row["all_fields_correct"] for row in condition_rows
            )
            / len(condition_rows),
            "field_accuracy": condition_correct / (len(condition_rows) * len(FIELDS)),
            "average_latency_seconds": sum(
                row["elapsed_seconds"] for row in condition_rows
            )
            / len(condition_rows),
        }

    print("\nOCR evaluation")
    print("=" * 72)
    print(f"Documents:             {metrics['document_count']}")
    print(
        "All-fields success:     "
        f"{metrics['documents_all_fields_correct']}/{metrics['document_count']} "
        f"({metrics['document_success_rate']:.2%})"
    )
    print(f"Overall field accuracy:{metrics['overall_field_accuracy']:>9.2%}")
    print(f"Average latency:       {metrics['average_latency_seconds']:>9.2f}s")
    for field in FIELDS:
        print(f"{field:<23}{metrics['field_accuracy'][field]:>9.2%}")
    print("\nBy condition")
    for condition, values in metrics["condition_metrics"].items():
        print(
            f"{condition:<16} field={values['field_accuracy']:.2%}, "
            f"document={values['document_success_rate']:.2%}, "
            f"latency={values['average_latency_seconds']:.2f}s"
        )

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    metrics_path = RESULTS_DIR / "ocr_metrics.json"
    details_path = RESULTS_DIR / "ocr_predictions.csv"
    metrics_path.write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with details_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    print(f"\nSaved metrics: {metrics_path}")
    print(f"Saved details: {details_path}")


if __name__ == "__main__":
    main()
