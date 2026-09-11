"""Build the fixed 90-invoice classification evaluation dataset.

The category labels are assigned by the dataset design. They must be reviewed
by a human before the dataset is described as a human-validated gold standard.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
sys.path.append(str(BACKEND_ROOT))

from scripts.seed_report_test_invoices import test_records  # noqa: E402


OUTPUT_PATH = BACKEND_ROOT / "evaluation" / "manual_labelled_invoices.json"

CATEGORY_RATIONALES = {
    "Environmental": (
        "The purchased product or service directly concerns energy, emissions, "
        "water, waste, circular materials, or environmental restoration."
    ),
    "Social": (
        "The purchased product or service directly concerns employees, health, "
        "safety, inclusion, skills, wellbeing, or community development."
    ),
    "Governance": (
        "The purchased product or service directly concerns corporate controls, "
        "ethics, compliance, risk, data governance, or board oversight."
    ),
    "Non-ESG": (
        "The invoice describes routine business expenditure without a sufficiently "
        "direct ESG purpose in the available description."
    ),
}

HARD_CASE_TERMS = {
    "data protection",
    "whistleblowing",
    "supplier code",
    "records-retention",
    "risk-register",
    "catering",
    "courier",
    "cleaning supplies",
    "promotional merchandise",
    "visitor chairs",
    "recycled-content packaging",
    "community",
    "diversity",
    "apprenticeship",
}


def build_dataset() -> list[dict]:
    dataset: list[dict] = []
    for index, (payload, expected_label) in enumerate(test_records(), start=1):
        record = payload.model_dump()
        description = str(record["description"])
        difficulty = (
            "hard"
            if any(term in description.casefold() for term in HARD_CASE_TERMS)
            else "clear"
        )
        record.update(
            {
                "dataset_id": f"MLI-{index:03d}",
                "invoice_number": f"EVAL-2026-{index:04d}",
                "expected_label": expected_label,
                "label_rationale": CATEGORY_RATIONALES[expected_label],
                "difficulty": difficulty,
                "label_source": "designer_assigned",
                "human_validation_status": "pending",
            }
        )
        dataset.append(record)
    return dataset


def validate_dataset(dataset: list[dict]) -> None:
    if len(dataset) != 90:
        raise ValueError(f"Expected 90 invoices, found {len(dataset)}")
    invoice_numbers = {record["invoice_number"] for record in dataset}
    if len(invoice_numbers) != 90:
        raise ValueError("Invoice numbers must be unique")
    labels = {record["expected_label"] for record in dataset}
    if labels != set(CATEGORY_RATIONALES):
        raise ValueError(f"Unexpected labels: {sorted(labels)}")


def main() -> None:
    dataset = build_dataset()
    validate_dataset(dataset)
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(
        json.dumps(dataset, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    counts: dict[str, int] = {}
    difficulty_counts: dict[str, int] = {}
    for record in dataset:
        label = record["expected_label"]
        difficulty = record["difficulty"]
        counts[label] = counts.get(label, 0) + 1
        difficulty_counts[difficulty] = difficulty_counts.get(difficulty, 0) + 1

    print(f"Created {OUTPUT_PATH}")
    print(f"Invoices: {len(dataset)}")
    print("Labels: " + ", ".join(f"{key}={value}" for key, value in counts.items()))
    print(
        "Difficulty: "
        + ", ".join(f"{key}={value}" for key, value in difficulty_counts.items())
    )
    print("Human validation status: pending")


if __name__ == "__main__":
    main()
