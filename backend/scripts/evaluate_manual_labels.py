"""Run the fixed labelled invoice dataset through the real RAG + LLM system.

Results are stored in an isolated SQLite database and printed as classification
metrics. Interrupted runs can be resumed because completed predictions are
loaded from the evaluation database unless ``--rerun`` is supplied.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
DEFAULT_DATASET = BACKEND_ROOT / "evaluation" / "manual_labelled_invoices.json"
DEFAULT_DATABASE = BACKEND_ROOT / "evaluation" / "classification_evaluation.db"
DEFAULT_RESULTS_DIR = BACKEND_ROOT / "evaluation" / "results"
PROMPT_VERSION = "manual_label_evaluation_v1"
CATEGORIES = ["Environmental", "Social", "Governance", "Non-ESG"]


@dataclass(frozen=True)
class Prediction:
    dataset_id: str
    invoice_number: str
    expected: str
    predicted: str
    confidence: str
    evidence_count: int
    reason: str
    difficulty: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate 90 labelled invoices with the real RAG + LLM pipeline."
    )
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--database", type=Path, default=DEFAULT_DATABASE)
    parser.add_argument("--results-dir", type=Path, default=DEFAULT_RESULTS_DIR)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--rerun", action="store_true")
    parser.add_argument(
        "--allow-unvalidated",
        action="store_true",
        help="Allow labels whose human_validation_status is still pending.",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate and summarise the dataset without calling the model.",
    )
    return parser.parse_args()


def load_dataset(path: Path, allow_unvalidated: bool) -> list[dict]:
    records = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(records, list) or not records:
        raise ValueError("Dataset must be a non-empty JSON array")

    required = {
        "dataset_id",
        "invoice_number",
        "supplier_name",
        "invoice_date",
        "amount",
        "currency",
        "description",
        "expected_label",
        "difficulty",
        "human_validation_status",
    }
    for index, record in enumerate(records, start=1):
        missing = required - set(record)
        if missing:
            raise ValueError(f"Record {index} is missing {sorted(missing)}")
        if record["expected_label"] not in CATEGORIES:
            raise ValueError(
                f"Record {index} has invalid label {record['expected_label']!r}"
            )

    if len({record["invoice_number"] for record in records}) != len(records):
        raise ValueError("Invoice numbers must be unique")
    pending = [
        record["dataset_id"]
        for record in records
        if record["human_validation_status"] != "validated"
    ]
    if pending and not allow_unvalidated:
        raise ValueError(
            f"{len(pending)} labels are not human-validated. Review the dataset and "
            "set human_validation_status to 'validated', or run explicitly with "
            "--allow-unvalidated for a preliminary synthetic evaluation."
        )
    return records


def print_dataset_summary(records: list[dict]) -> None:
    labels = Counter(record["expected_label"] for record in records)
    difficulties = Counter(record["difficulty"] for record in records)
    validation = Counter(record["human_validation_status"] for record in records)
    print("\nDataset summary")
    print("-" * 72)
    print(f"Invoices: {len(records)}")
    print("Labels: " + ", ".join(f"{key}={labels[key]}" for key in CATEGORIES))
    print("Difficulty: " + ", ".join(f"{k}={v}" for k, v in difficulties.items()))
    print("Human validation: " + ", ".join(f"{k}={v}" for k, v in validation.items()))


def configure_database(path: Path) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    os.environ["DATABASE_PATH"] = str(path)
    os.environ["LANGGRAPH_CHECKPOINT_PATH"] = str(
        path.with_name(f"{path.stem}_checkpoints.db")
    )
    sys.path.append(str(BACKEND_ROOT))


def run_evaluation(args: argparse.Namespace, records: list[dict]) -> list[Prediction]:
    configure_database(args.database)

    from app.db.database import db_session, init_db
    from app.schemas import InvoiceClassification, InvoiceCreate
    from app.services.classification_service import classify_invoice
    from app.services.invoice_service import create_invoice, save_classification

    init_db()

    def existing_prediction(invoice_number: str) -> tuple[str, str, int, str] | None:
        with db_session() as db:
            row = db.execute(
                """
                SELECT c.category, c.confidence, c.evidence_json, c.reason
                FROM invoices i
                JOIN invoice_classifications c ON c.invoice_id = i.id
                WHERE i.invoice_number = ?
                  AND c.invoice_version = i.version
                  AND c.prompt_version = ?
                ORDER BY c.created_at DESC, c.id DESC
                LIMIT 1
                """,
                (invoice_number, PROMPT_VERSION),
            ).fetchone()
        if row is None:
            return None
        evidence = json.loads(row["evidence_json"] or "[]")
        return row["category"], row["confidence"], len(evidence), row["reason"]

    def classify_record(record: dict) -> Prediction:
        payload = InvoiceCreate(
            invoice_number=record["invoice_number"],
            supplier_name=record["supplier_name"],
            buyer_name=record.get("buyer_name"),
            invoice_date=record["invoice_date"],
            amount=record["amount"],
            currency=record["currency"],
            description=record["description"],
        )
        invoice = create_invoice(payload)
        stored = None if args.rerun else existing_prediction(record["invoice_number"])
        if stored is None:
            decision = classify_invoice(invoice.id)
            reviewed = InvoiceClassification(
                category=decision.category,
                status="Reviewed",
                confidence=decision.confidence,
                reason=decision.reason,
                evidence=decision.evidence,
            )
            save_classification(
                invoice_id=invoice.id,
                classification=reviewed,
                model_name="rag-llm-evaluation",
                prompt_version=PROMPT_VERSION,
            )
            stored = (
                decision.category,
                decision.confidence,
                len(decision.evidence),
                decision.reason,
            )

        predicted, confidence, evidence_count, reason = stored
        return Prediction(
            dataset_id=record["dataset_id"],
            invoice_number=record["invoice_number"],
            expected=record["expected_label"],
            predicted=predicted,
            confidence=confidence,
            evidence_count=evidence_count,
            reason=reason,
            difficulty=record["difficulty"],
        )

    workers = max(1, min(args.workers, 8))
    selected = records[: args.limit] if args.limit else records
    predictions: list[Prediction] = []
    failures: list[tuple[str, str]] = []
    print(f"\nClassifying {len(selected)} invoices with {workers} worker(s)...")
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(classify_record, record): record for record in selected}
        for completed, future in enumerate(as_completed(futures), start=1):
            record = futures[future]
            try:
                prediction = future.result()
                predictions.append(prediction)
                marker = "OK" if prediction.expected == prediction.predicted else "MISS"
                print(
                    f"[{completed:02d}/{len(selected):02d}] {marker:<4} "
                    f"{prediction.invoice_number}: {prediction.expected} -> "
                    f"{prediction.predicted}"
                )
            except Exception as exc:
                failures.append((record["invoice_number"], str(exc)))
                print(
                    f"[{completed:02d}/{len(selected):02d}] FAIL "
                    f"{record['invoice_number']}: {exc}",
                    file=sys.stderr,
                )

    if failures:
        print("\nFailures:", file=sys.stderr)
        for invoice_number, error in failures:
            print(f"- {invoice_number}: {error}", file=sys.stderr)
        raise RuntimeError(
            f"{len(failures)} invoice(s) failed. Rerun the command to resume."
        )
    return sorted(predictions, key=lambda item: item.dataset_id)


def safe_ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 0.0


def calculate_metrics(predictions: list[Prediction]) -> dict:
    matrix = {actual: {predicted: 0 for predicted in CATEGORIES} for actual in CATEGORIES}
    for item in predictions:
        matrix[item.expected][item.predicted] += 1

    correct = sum(item.expected == item.predicted for item in predictions)
    per_class = {}
    for category in CATEGORIES:
        true_positive = matrix[category][category]
        false_positive = sum(
            matrix[actual][category] for actual in CATEGORIES if actual != category
        )
        false_negative = sum(
            matrix[category][predicted]
            for predicted in CATEGORIES
            if predicted != category
        )
        support = sum(matrix[category].values())
        precision = safe_ratio(true_positive, true_positive + false_positive)
        recall = safe_ratio(true_positive, true_positive + false_negative)
        f1 = safe_ratio(2 * precision * recall, precision + recall)
        per_class[category] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "support": support,
        }

    macro_precision = sum(item["precision"] for item in per_class.values()) / 4
    macro_recall = sum(item["recall"] for item in per_class.values()) / 4
    macro_f1 = sum(item["f1"] for item in per_class.values()) / 4
    evidence_coverage = safe_ratio(
        sum(item.evidence_count > 0 for item in predictions),
        len(predictions),
    )
    return {
        "sample_count": len(predictions),
        "correct": correct,
        "accuracy": safe_ratio(correct, len(predictions)),
        "macro_precision": macro_precision,
        "macro_recall": macro_recall,
        "macro_f1": macro_f1,
        "evidence_coverage": evidence_coverage,
        "per_class": per_class,
        "confusion_matrix": matrix,
    }


def print_metrics(metrics: dict, predictions: list[Prediction]) -> None:
    print("\nClassification evaluation")
    print("=" * 72)
    print(f"Samples:          {metrics['sample_count']}")
    print(f"Correct:          {metrics['correct']}")
    print(f"Accuracy:         {metrics['accuracy']:.2%}")
    print(f"Macro precision:  {metrics['macro_precision']:.2%}")
    print(f"Macro recall:     {metrics['macro_recall']:.2%}")
    print(f"Macro F1:         {metrics['macro_f1']:.2%}")
    print(f"Evidence coverage:{metrics['evidence_coverage']:>8.2%}")

    print("\nPer-class metrics")
    print(f"{'Category':<16} {'Precision':>10} {'Recall':>10} {'F1':>10} {'Support':>9}")
    for category in CATEGORIES:
        item = metrics["per_class"][category]
        print(
            f"{category:<16} {item['precision']:>10.2%} {item['recall']:>10.2%} "
            f"{item['f1']:>10.2%} {item['support']:>9}"
        )

    print("\nConfusion matrix (rows=actual, columns=predicted)")
    short = {"Environmental": "E", "Social": "S", "Governance": "G", "Non-ESG": "N"}
    print(f"{'Actual':<16}" + "".join(f"{short[c]:>7}" for c in CATEGORIES))
    for actual in CATEGORIES:
        values = "".join(
            f"{metrics['confusion_matrix'][actual][predicted]:>7}"
            for predicted in CATEGORIES
        )
        print(f"{actual:<16}{values}")

    errors = [item for item in predictions if item.expected != item.predicted]
    print(f"\nMisclassified invoices: {len(errors)}")
    for item in errors:
        print(
            f"- {item.invoice_number}: {item.expected} -> {item.predicted} "
            f"({item.confidence}, evidence={item.evidence_count})"
        )


def save_results(
    results_dir: Path,
    metrics: dict,
    predictions: Iterable[Prediction],
) -> None:
    results_dir.mkdir(parents=True, exist_ok=True)
    metrics_path = results_dir / "classification_metrics.json"
    predictions_path = results_dir / "classification_predictions.csv"
    metrics_path.write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    with predictions_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(
            stream,
            fieldnames=[
                "dataset_id",
                "invoice_number",
                "expected",
                "predicted",
                "correct",
                "confidence",
                "evidence_count",
                "difficulty",
                "reason",
            ],
        )
        writer.writeheader()
        for item in predictions:
            writer.writerow(
                {
                    **item.__dict__,
                    "correct": item.expected == item.predicted,
                }
            )
    print(f"\nSaved metrics: {metrics_path}")
    print(f"Saved predictions: {predictions_path}")


def main() -> None:
    args = parse_args()
    if not args.dataset.exists():
        raise SystemExit(
            f"Dataset not found: {args.dataset}\n"
            "Run: python scripts/build_manual_label_dataset.py"
        )
    try:
        records = load_dataset(args.dataset, args.allow_unvalidated)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    print_dataset_summary(records)
    if args.validate_only:
        return

    predictions = run_evaluation(args, records)
    metrics = calculate_metrics(predictions)
    print_metrics(metrics, predictions)
    save_results(args.results_dir, metrics, predictions)


if __name__ == "__main__":
    main()
