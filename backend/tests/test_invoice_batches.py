import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.invoices import batch_router
from app.core.config import settings
from app.db.database import db_session, get_connection, init_db, utc_now
from app.schemas import (
    InvoiceBatchCreate,
    InvoiceClassification,
    InvoiceCreate,
)
from app.services.classification_job_service import (
    claim_next_classification_job,
    enqueue_unclassified_invoices,
    process_claimed_classification_job,
    run_classification_worker_once,
)
from app.services.invoice_service import (
    create_invoice,
    create_invoice_batch,
    get_invoice,
    get_invoice_batch,
    save_classification,
)
from app.services.summary_service import get_dashboard_summary


def invoice_payload(description: str = "Solar panel maintenance") -> InvoiceCreate:
    return InvoiceCreate(
        invoice_number="INV-100",
        supplier_name="Green Supplier",
        buyer_name="Example Buyer",
        invoice_date="2026-07-01",
        amount=1250.0,
        currency="MYR",
        description=description,
    )


class InvoiceBatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_database_path = settings.database_path
        settings.database_path = Path(self.temp_dir.name) / "test.db"
        init_db()

    def tearDown(self) -> None:
        settings.database_path = self.original_database_path
        self.temp_dir.cleanup()

    def test_batch_tracks_create_unchanged_update_and_current_classification(self):
        first = create_invoice_batch(InvoiceBatchCreate(invoices=[invoice_payload()]))
        self.assertEqual(first.status, "processing")
        self.assertEqual(first.created_count, 1)
        self.assertEqual(first.items[0].upsert_status, "created")
        self.assertEqual(first.items[0].classification_status, "queued")
        invoice_id = first.items[0].invoice_id

        save_classification(
            invoice_id=invoice_id,
            classification=InvoiceClassification(
                category="Environmental",
                status="Pending",
                confidence="high",
                reason="Supported by renewable energy evidence.",
            ),
            model_name="test-model",
        )
        completed = get_invoice_batch(first.batch_id)
        self.assertEqual(completed.status, "completed")
        self.assertEqual(completed.classification.completed, 1)

        unchanged = create_invoice_batch(
            InvoiceBatchCreate(invoices=[invoice_payload()])
        )
        self.assertEqual(unchanged.items[0].upsert_status, "unchanged")
        self.assertEqual(unchanged.items[0].classification_status, "completed")

        updated = create_invoice_batch(
            InvoiceBatchCreate(invoices=[invoice_payload("Wind turbine maintenance")])
        )
        self.assertEqual(updated.items[0].upsert_status, "updated")
        self.assertEqual(updated.items[0].invoice_version, 2)
        self.assertEqual(updated.items[0].classification_status, "queued")

        current = get_invoice(invoice_id)
        self.assertEqual(current.version, 2)
        self.assertIsNone(current.classification)
        self.assertFalse(current.processed_by_llm)
        with db_session() as db:
            versions = db.execute(
                """
                SELECT invoice_version FROM invoice_classifications
                WHERE invoice_id = ? ORDER BY invoice_version
                """,
                (invoice_id,),
            ).fetchall()
        self.assertEqual([row["invoice_version"] for row in versions], [1])

    def test_single_invoice_create_enqueues_and_does_not_bump_unchanged_version(self):
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

    def test_worker_claim_is_atomic_and_retries_at_most_three_times(self):
        batch = create_invoice_batch(InvoiceBatchCreate(invoices=[invoice_payload()]))

        def fail(_invoice_id: str) -> None:
            raise RuntimeError("temporary model failure")

        for expected_attempt in (1, 2):
            self.assertFalse(
                run_classification_worker_once(
                    classify_fn=fail,
                    worker_id=f"worker-{expected_attempt}",
                    retry_delay_seconds=0,
                )
            )
            with db_session() as db:
                job = db.execute("SELECT * FROM classification_jobs").fetchone()
            self.assertEqual(job["status"], "queued")
            self.assertEqual(job["attempts"], expected_attempt)

        self.assertFalse(
            run_classification_worker_once(
                classify_fn=fail,
                worker_id="worker-3",
                retry_delay_seconds=0,
            )
        )
        with db_session() as db:
            job = db.execute("SELECT * FROM classification_jobs").fetchone()
        self.assertEqual(job["status"], "failed")
        self.assertEqual(job["attempts"], 3)
        self.assertIn("temporary model failure", job["last_error"])
        progress = get_invoice_batch(batch.batch_id)
        self.assertEqual(progress.status, "completed_with_errors")
        self.assertEqual(progress.classification.failed, 1)

        self.assertEqual(enqueue_unclassified_invoices(), 1)
        with db_session() as db:
            retried = db.execute("SELECT * FROM classification_jobs").fetchone()
        self.assertEqual(retried["status"], "queued")
        self.assertEqual(retried["attempts"], 0)
        self.assertIsNone(retried["last_error"])

    def test_two_workers_cannot_claim_the_same_job(self):
        create_invoice_batch(
            InvoiceBatchCreate(
                invoices=[
                    invoice_payload(),
                    InvoiceCreate(
                        **{
                            **invoice_payload().model_dump(),
                            "invoice_number": "INV-101",
                        }
                    ),
                ]
            )
        )
        first = claim_next_classification_job("worker-a")
        second = claim_next_classification_job("worker-b")
        third = claim_next_classification_job("worker-c")
        self.assertNotEqual(first["id"], second["id"])
        self.assertIsNone(third)

    def test_expired_worker_cannot_save_over_new_lease_owner(self):
        create_invoice_batch(InvoiceBatchCreate(invoices=[invoice_payload()]))
        stale_job = claim_next_classification_job("worker-a")
        with db_session() as db:
            db.execute(
                """
                UPDATE classification_jobs
                SET status = 'queued', worker_id = NULL, lease_expires_at = NULL
                WHERE id = ?
                """,
                (stale_job["id"],),
            )
        current_job = claim_next_classification_job("worker-b")

        def stale_classification(invoice_id: str) -> None:
            save_classification(
                invoice_id,
                InvoiceClassification(
                    category="Environmental",
                    status="Pending",
                    confidence="medium",
                    reason="A stale result that must not be saved.",
                ),
                model_name="test-model",
            )

        self.assertFalse(
            process_claimed_classification_job(
                stale_job,
                classify_fn=stale_classification,
                retry_delay_seconds=0,
            )
        )
        with db_session() as db:
            stored_job = db.execute(
                "SELECT * FROM classification_jobs WHERE id = ?",
                (stale_job["id"],),
            ).fetchone()
            classification_count = db.execute(
                "SELECT COUNT(*) FROM invoice_classifications"
            ).fetchone()[0]
        self.assertEqual(stored_job["worker_id"], current_job["worker_id"])
        self.assertEqual(stored_job["status"], "running")
        self.assertEqual(classification_count, 0)

    def test_worker_success_completes_batch(self):
        batch = create_invoice_batch(InvoiceBatchCreate(invoices=[invoice_payload()]))

        def classify(invoice_id: str) -> None:
            save_classification(
                invoice_id,
                InvoiceClassification(
                    category="Environmental",
                    status="Pending",
                    confidence="medium",
                    reason="Test classification.",
                ),
                model_name="test-model",
            )

        self.assertTrue(
            run_classification_worker_once(
                classify_fn=classify,
                worker_id="success-worker",
            )
        )
        progress = get_invoice_batch(batch.batch_id)
        self.assertEqual(progress.status, "completed")
        self.assertEqual(progress.classification.completed, 1)

    def test_failed_item_is_recorded_without_losing_batch(self):
        with patch(
            "app.services.invoice_service._upsert_invoice_in_db",
            side_effect=RuntimeError("bad item"),
        ):
            batch = create_invoice_batch(
                InvoiceBatchCreate(invoices=[invoice_payload()])
            )
        self.assertEqual(batch.status, "completed_with_errors")
        self.assertEqual(batch.failed_count, 1)
        self.assertEqual(batch.items[0].upsert_status, "failed")
        self.assertIn("bad item", batch.items[0].error)

    def test_unclassified_invoice_is_not_counted_as_non_esg(self):
        create_invoice(invoice_payload())
        summary = get_dashboard_summary("2026-01-01", "2026-12-31")
        self.assertEqual(summary.total_invoices.count, 1)
        self.assertEqual(summary.unclassified_count, 1)
        self.assertEqual(summary.categories["Non-ESG"].count, 0)

    def test_dashboard_does_not_sum_different_currencies(self):
        create_invoice(invoice_payload())
        create_invoice(
            InvoiceCreate(
                **{
                    **invoice_payload().model_dump(),
                    "invoice_number": "INV-USD",
                    "amount": 25,
                    "currency": "USD",
                }
            )
        )

        summary = get_dashboard_summary("2026-01-01", "2026-12-31")
        self.assertEqual(summary.total_invoices.currency, "MIXED")
        self.assertEqual(summary.total_invoices.amount, 0)
        self.assertEqual(
            summary.total_invoices.amounts_by_currency,
            {"MYR": 1250.0, "USD": 25.0},
        )

    def test_batch_api_contract_and_one_hundred_invoice_import(self):
        api = FastAPI()
        api.include_router(batch_router)
        client = TestClient(api)
        invoices = [
            {
                **invoice_payload().model_dump(),
                "invoice_number": f"INV-{index:03d}",
            }
            for index in range(100)
        ]
        response = client.post("/invoice-batches", json={"invoices": invoices})
        self.assertEqual(response.status_code, 202)
        body = response.json()
        self.assertEqual(body["total_count"], 100)
        self.assertEqual(body["created_count"], 100)
        self.assertEqual(body["classification"]["queued"], 100)
        self.assertEqual(len(body["items"]), 100)

        progress = client.get(f"/invoice-batches/{body['batch_id']}")
        self.assertEqual(progress.status_code, 200)
        self.assertEqual(progress.json()["status"], "processing")
        with db_session() as db:
            self.assertEqual(
                db.execute("SELECT COUNT(*) FROM classification_jobs").fetchone()[0],
                100,
            )

    def test_batch_api_reports_invalid_item_without_rejecting_valid_items(self):
        api = FastAPI()
        api.include_router(batch_router)
        client = TestClient(api)
        response = client.post(
            "/invoice-batches",
            json={
                "invoices": [
                    invoice_payload().model_dump(),
                    {
                        "invoice_number": "INV-BAD",
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
        self.assertEqual(body["items"][1]["invoice_number"], "INV-BAD")
        self.assertEqual(body["items"][1]["upsert_status"], "failed")

    def test_schema_migrates_legacy_database_and_adds_report_persistence(self):
        legacy_path = Path(self.temp_dir.name) / "legacy.db"
        settings.database_path = legacy_path
        connection = sqlite3.connect(legacy_path)
        connection.executescript(
            """
            CREATE TABLE invoices (
                id TEXT PRIMARY KEY, invoice_number TEXT NOT NULL UNIQUE,
                supplier_name TEXT NOT NULL, buyer_name TEXT,
                invoice_date TEXT NOT NULL, amount REAL NOT NULL,
                currency TEXT NOT NULL, description TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE invoice_classifications (
                id TEXT PRIMARY KEY, invoice_id TEXT NOT NULL,
                category TEXT NOT NULL, status TEXT NOT NULL,
                confidence TEXT NOT NULL, reason TEXT NOT NULL,
                evidence_json TEXT NOT NULL, model_name TEXT NOT NULL,
                prompt_version TEXT NOT NULL, created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE reports (
                id TEXT PRIMARY KEY, title TEXT NOT NULL,
                period_start TEXT NOT NULL, period_end TEXT NOT NULL,
                status TEXT NOT NULL, content TEXT NOT NULL,
                model_name TEXT NOT NULL, created_at TEXT NOT NULL
            );
            INSERT INTO invoices VALUES (
                'legacy-id', 'LEGACY-1', 'Supplier', NULL, '2026-01-01',
                10.0, 'MYR', 'Legacy invoice', '2026-01-01T00:00:00+00:00'
            );
            """
        )
        connection.commit()
        connection.close()

        init_db()
        with db_session() as db:
            invoice = db.execute(
                "SELECT version, content_hash, updated_at FROM invoices"
            ).fetchone()
            report_columns = {
                row["name"] for row in db.execute("PRAGMA table_info(reports)")
            }
            tables = {
                row["name"]
                for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
            journal_mode = db.execute("PRAGMA journal_mode").fetchone()[0]
            busy_timeout = db.execute("PRAGMA busy_timeout").fetchone()[0]

        self.assertEqual(invoice["version"], 1)
        self.assertEqual(len(invoice["content_hash"]), 64)
        self.assertTrue(invoice["updated_at"])
        self.assertIn("source_run_id", report_columns)
        self.assertIn("report_agent_runs", tables)
        self.assertIn("report_agent_steps", tables)
        self.assertEqual(journal_mode.lower(), "wal")
        self.assertGreaterEqual(busy_timeout, 10_000)


if __name__ == "__main__":
    unittest.main()
