# Classification Evaluation

This folder contains a fixed synthetic invoice dataset and the results produced
by the real RAG + LLM classification pipeline.

## 1. Build and inspect the dataset

```powershell
python scripts/build_manual_label_dataset.py
python scripts/evaluate_manual_labels.py --allow-unvalidated --validate-only
```

The generated labels are designer-assigned. Before they are described as a
human-validated gold standard in the FYP report, review every record in
`manual_labelled_invoices.json` and change `human_validation_status` from
`pending` to `validated` only when its expected label is accepted.

## 2. Run the real evaluation

```powershell
python scripts/evaluate_manual_labels.py --allow-unvalidated --workers 3
```

The script uses `classification_evaluation.db`, not the normal application
database. It can safely resume an interrupted run. Use `--rerun` only when a
fresh set of LLM predictions is intentionally required.

Terminal output and saved results include accuracy, macro precision, macro
recall, macro F1, per-class metrics, a confusion matrix, evidence coverage, and
the misclassified invoice list.
