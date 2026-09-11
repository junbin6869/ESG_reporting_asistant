import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.invoices import batch_router
from app.core.config import settings
from app.db.database import db_session, init_db
from app.schemas import InvoiceBatchCreate, InvoiceClassification, InvoiceCreate
from app.services.classification_job_service import run_classification_worker_once
from app.services.invoice_service import (
    create_invoice,
    create_invoice_batch,
    get_invoice,
    get_invoice_batch,
    save_classification,
)


def invoice_payload(description: str = "Solar panel maintenance") -> InvoiceCreate:
    return InvoiceCreate(
        invoice_number="CORE-INV-100",
        supplier_name="Green Supplier Sdn Bhd",
        buyer_name="Example Buyer Sdn Bhd",
        invoice_date="2026-07-01",
        amount=1250.0,
        currency="MYR",
        description=description,
    )


class CoreInvoicePipelineTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_database_path = settings.database_path
        settings.database_path = Path(self.temp_dir.name) / "core-tests.db"
        init_db()

    def tearDown(self) -> None:
        settings.database_path = self.original_database_path
        self.temp_dir.cleanup()

    def test_invoice_create_is_idempotent_and_enqueues_once(self):
        first = create_invoice(invoice_payload())
        second = create_invoice(invoice_payload())

        self.assertEqual(first.id, second.id)
        self.assertEqual(second.version, 1)
        self.assertEqual(second.classification_status, "queued")
        with db_session() as db:
            count = db.execute(
                "SELECT COUNT(*) AS count FROM classification_jobs"
            ).fetchone()["count"]
        self.assertEqual(count, 1)

    def test_invoice_update_increments_version_and_requires_reclassification(self):
        first = create_invoice(invoice_payload())
        save_classification(
            invoice_id=first.id,
            classification=InvoiceClassification(
                category="Environmental",
                status="Reviewed",
                confidence="high",
                reason="Renewable-energy activity.",
            ),
            model_name="core-test-model",
        )

        updated = create_invoice(invoice_payload("Wind turbine maintenance"))
        current = get_invoice(first.id)

        self.assertEqual(updated.version, 2)
        self.assertEqual(updated.classification_status, "queued")
        self.assertIsNone(current.classification)

    def test_worker_retries_three_times_then_marks_batch_failed(self):
        batch = create_invoice_batch(InvoiceBatchCreate(invoices=[invoice_payload()]))

        def fail(_invoice_id: str) -> None:
            raise RuntimeError("temporary model failure")

        for attempt in range(1, 4):
            self.assertFalse(
                run_classification_worker_once(
                    classify_fn=fail,
                    worker_id=f"core-worker-{attempt}",
                    retry_delay_seconds=0,
                )
            )

        with db_session() as db:
            job = db.execute("SELECT * FROM classification_jobs").fetchone()
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["attempts"], 3)
        self.assertIn("temporary model failure", job["last_error"])
        self.assertEqual(get_invoice_batch(batch.batch_id).status, "completed_with_errors")

    def test_batch_api_accepts_valid_item_and_reports_invalid_item(self):
        api = FastAPI()
        api.include_router(batch_router)
        client = TestClient(api)
        response = client.post(
            "/invoice-batches",
            json={
                "invoices": [
                    invoice_payload().model_dump(),
                    {
                        "invoice_number": "CORE-INV-BAD",
                        "supplier_name": "Broken Supplier",
                        "invoice_date": "not-a-date",
                        "amount": -1,
                        "currency": "MYR",
                        "description": "Invalid record",
                    },
                ]
            },
        )

        self.assertEqual(response.status_code, 202)
        body = response.json()
        self.assertEqual(body["created_count"], 1)
        self.assertEqual(body["failed_count"], 1)
        self.assertEqual(body["items"][1]["upsert_status"], "failed")


if __name__ == "__main__":
    unittest.main()
