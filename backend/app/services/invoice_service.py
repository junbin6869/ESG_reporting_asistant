import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from app.db.database import db_session, decode_json, encode_json, utc_now
from app.schemas import (
    BatchClassificationProgress,
    InvoiceBatchCreate,
    InvoiceBatchItemResult,
    InvoiceBatchResponse,
    InvoiceClassification,
    InvoiceCreate,
    InvoiceRow,
    ReviewClassificationRequest,
)


_expected_classification_version: ContextVar[
    tuple[str, int, str | None, str | None, int | None] | None
] = ContextVar(
    "expected_classification_version",
    default=None,
)


@contextmanager
def classification_for_invoice_version(
    invoice_id: str,
    invoice_version: int,
    *,
    job_id: str | None = None,
    worker_id: str | None = None,
    attempt: int | None = None,
) -> Iterator[None]:
    """Bind a worker classification result to the version it actually read."""
    token = _expected_classification_version.set(
        (invoice_id, invoice_version, job_id, worker_id, attempt)
    )
    try:
        yield
    finally:
        _expected_classification_version.reset(token)


def invoice_content_hash(payload: InvoiceCreate) -> str:
    canonical = payload.model_dump()
    canonical["amount"] = float(canonical["amount"])
    serialized = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def row_to_invoice(
    row: sqlite3.Row,
    classification_row: sqlite3.Row | None,
    job_status: str | None = None,
) -> InvoiceRow:
    classification = None
    if classification_row:
        status = classification_row["status"]
        if status not in {"Pending", "Reviewed"}:
            status = "Reviewed"
        classification = InvoiceClassification(
            category=classification_row["category"],
            status=status,
            confidence=classification_row["confidence"],
            reason=classification_row["reason"],
            evidence=decode_json(classification_row["evidence_json"], []),
        )

    classification_status = "completed" if classification else job_status
    if classification_status not in {"queued", "running", "completed", "failed"}:
        classification_status = None

    return InvoiceRow(
        id=row["id"],
        invoice_number=row["invoice_number"],
        supplier_name=row["supplier_name"],
        buyer_name=row["buyer_name"],
        invoice_date=row["invoice_date"],
        amount=row["amount"],
        currency=row["currency"],
        description=row["description"],
        version=int(row["version"]),
        updated_at=row["updated_at"],
        processed_by_llm=classification is not None,
        classification_status=classification_status,
        classification=classification,
    )


def _latest_classification(
    db: sqlite3.Connection,
    invoice_id: str,
    invoice_version: int,
) -> sqlite3.Row | None:
    return db.execute(
        """
        SELECT *
        FROM invoice_classifications
        WHERE invoice_id = ? AND invoice_version = ?
        ORDER BY created_at DESC, id DESC
        LIMIT 1
        """,
        (invoice_id, invoice_version),
    ).fetchone()


def _current_job_status(
    db: sqlite3.Connection,
    invoice_id: str,
    invoice_version: int,
) -> str | None:
    row = db.execute(
        """
        SELECT status
        FROM classification_jobs
        WHERE invoice_id = ? AND invoice_version = ?
        """,
        (invoice_id, invoice_version),
    ).fetchone()
    return row["status"] if row else None


def list_invoices() -> list[InvoiceRow]:
    with db_session() as db:
        invoice_rows = db.execute(
            """
            SELECT *
            FROM invoices
            ORDER BY invoice_date DESC, created_at DESC
            """
        ).fetchall()
        result = []
        for row in invoice_rows:
            version = int(row["version"])
            result.append(
                row_to_invoice(
                    row,
                    _latest_classification(db, row["id"], version),
                    _current_job_status(db, row["id"], version),
                )
            )
    return result


def get_invoice(invoice_id: str) -> InvoiceRow | None:
    with db_session() as db:
        row = db.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
        if not row:
            return None
        version = int(row["version"])
        classification_row = _latest_classification(db, invoice_id, version)
        job_status = _current_job_status(db, invoice_id, version)
    return row_to_invoice(row, classification_row, job_status)


def _upsert_invoice_in_db(
    db: sqlite3.Connection,
    payload: InvoiceCreate,
) -> tuple[sqlite3.Row, str, str]:
    from app.services.classification_job_service import enqueue_classification_job

    now = utc_now()
    content_hash = invoice_content_hash(payload)
    existing = db.execute(
        "SELECT * FROM invoices WHERE invoice_number = ?",
        (payload.invoice_number,),
    ).fetchone()

    if not existing:
        invoice_id = str(uuid.uuid4())
        version = 1
        db.execute(
            """
            INSERT INTO invoices (
                id, invoice_number, supplier_name, buyer_name, invoice_date,
                amount, currency, description, version, content_hash,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                invoice_id,
                payload.invoice_number,
                payload.supplier_name,
                payload.buyer_name,
                payload.invoice_date,
                payload.amount,
                payload.currency,
                payload.description,
                version,
                content_hash,
                now,
                now,
            ),
        )
        upsert_status = "created"
    elif existing["content_hash"] == content_hash:
        invoice_id = existing["id"]
        version = int(existing["version"])
        upsert_status = "unchanged"
    else:
        invoice_id = existing["id"]
        version = int(existing["version"]) + 1
        db.execute(
            """
            UPDATE invoices
            SET supplier_name = ?, buyer_name = ?, invoice_date = ?, amount = ?,
                currency = ?, description = ?, version = ?, content_hash = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                payload.supplier_name,
                payload.buyer_name,
                payload.invoice_date,
                payload.amount,
                payload.currency,
                payload.description,
                version,
                content_hash,
                now,
                invoice_id,
            ),
        )
        upsert_status = "updated"

    classification_row = _latest_classification(db, invoice_id, version)
    if classification_row:
        classification_status = "completed"
    else:
        classification_status = enqueue_classification_job(
            db,
            invoice_id=invoice_id,
            invoice_version=version,
        )

    row = db.execute("SELECT * FROM invoices WHERE id = ?", (invoice_id,)).fetchone()
    return row, upsert_status, classification_status


def create_invoice(payload: InvoiceCreate) -> InvoiceRow:
    with db_session() as db:
        row, _, _ = _upsert_invoice_in_db(db, payload)
        invoice_id = row["id"]

    invoice = get_invoice(invoice_id)
    if invoice is None:  # pragma: no cover - protected by the transaction above
        raise RuntimeError("Invoice disappeared after creation")
    return invoice


def _refresh_batch_record(db: sqlite3.Connection, batch_id: str) -> None:
    counts = db.execute(
        """
        SELECT
            COUNT(*) AS total_count,
            SUM(CASE WHEN upsert_status = 'created' THEN 1 ELSE 0 END) AS created_count,
            SUM(CASE WHEN upsert_status = 'updated' THEN 1 ELSE 0 END) AS updated_count,
            SUM(CASE WHEN upsert_status = 'unchanged' THEN 1 ELSE 0 END) AS unchanged_count,
            SUM(CASE WHEN upsert_status = 'failed' THEN 1 ELSE 0 END) AS failed_count,
            SUM(CASE WHEN classification_status = 'queued' THEN 1 ELSE 0 END) AS queued_count,
            SUM(CASE WHEN classification_status = 'running' THEN 1 ELSE 0 END) AS running_count,
            SUM(CASE WHEN classification_status = 'failed' THEN 1 ELSE 0 END) AS classification_failed_count
        FROM invoice_batch_items
        WHERE batch_id = ?
        """,
        (batch_id,),
    ).fetchone()
    if not counts:
        return

    if int(counts["queued_count"] or 0) or int(counts["running_count"] or 0):
        status = "processing"
    elif int(counts["failed_count"] or 0) or int(
        counts["classification_failed_count"] or 0
    ):
        status = "completed_with_errors"
    else:
        status = "completed"

    values = (
        status,
        int(counts["total_count"] or 0),
        int(counts["created_count"] or 0),
        int(counts["updated_count"] or 0),
        int(counts["unchanged_count"] or 0),
        int(counts["failed_count"] or 0),
    )
    current = db.execute(
        """
        SELECT status, total_count, created_count, updated_count,
               unchanged_count, failed_count
        FROM invoice_batches WHERE id = ?
        """,
        (batch_id,),
    ).fetchone()
    if current and values == tuple(current):
        return

    db.execute(
        """
        UPDATE invoice_batches
        SET status = ?, total_count = ?, created_count = ?, updated_count = ?,
            unchanged_count = ?, failed_count = ?, updated_at = ?
        WHERE id = ?
        """,
        (
            *values,
            utc_now(),
            batch_id,
        ),
    )


def _sync_batch_item_statuses(db: sqlite3.Connection, batch_id: str) -> None:
    rows = db.execute(
        """
        SELECT * FROM invoice_batch_items
        WHERE batch_id = ? AND upsert_status != 'failed'
        ORDER BY position
        """,
        (batch_id,),
    ).fetchall()
    now = utc_now()
    for row in rows:
        classification = _latest_classification(
            db,
            row["invoice_id"],
            int(row["invoice_version"]),
        )
        if classification:
            status = "completed"
            error = None
        else:
            job = db.execute(
                """
                SELECT status, last_error FROM classification_jobs
                WHERE invoice_id = ? AND invoice_version = ?
                """,
                (row["invoice_id"], row["invoice_version"]),
            ).fetchone()
            status = job["status"] if job else "failed"
            error = (
                job["last_error"]
                if job and job["status"] == "failed"
                else ("Classification job is missing." if not job else None)
            )
        if row["classification_status"] != status or row["error"] != error:
            db.execute(
                """
                UPDATE invoice_batch_items
                SET classification_status = ?, error = ?, updated_at = ?
                WHERE id = ?
                """,
                (status, error, now, row["id"]),
            )


def _batch_response(db: sqlite3.Connection, batch_id: str) -> InvoiceBatchResponse | None:
    batch = db.execute(
        "SELECT * FROM invoice_batches WHERE id = ?",
        (batch_id,),
    ).fetchone()
    if not batch:
        return None
    item_rows = db.execute(
        """
        SELECT * FROM invoice_batch_items
        WHERE batch_id = ?
        ORDER BY position
        """,
        (batch_id,),
    ).fetchall()
    classification_counts = {status: 0 for status in ("queued", "running", "completed", "failed")}
    items = []
    for row in item_rows:
        classification_counts[row["classification_status"]] += 1
        items.append(
            InvoiceBatchItemResult(
                position=row["position"],
                invoice_number=row["invoice_number"],
                invoice_id=row["invoice_id"],
                invoice_version=row["invoice_version"],
                upsert_status=row["upsert_status"],
                classification_status=row["classification_status"],
                error=row["error"],
            )
        )
    return InvoiceBatchResponse(
        batch_id=batch["id"],
        status=batch["status"],
        total_count=batch["total_count"],
        created_count=batch["created_count"],
        updated_count=batch["updated_count"],
        unchanged_count=batch["unchanged_count"],
        failed_count=batch["failed_count"],
        classification=BatchClassificationProgress(**classification_counts),
        items=items,
        created_at=batch["created_at"],
        updated_at=batch["updated_at"],
    )


def create_invoice_batch(payload: InvoiceBatchCreate) -> InvoiceBatchResponse:
    batch_id = str(uuid.uuid4())
    now = utc_now()
    with db_session() as db:
        db.execute(
            """
            INSERT INTO invoice_batches (
                id, status, total_count, created_at, updated_at
            ) VALUES (?, 'processing', ?, ?, ?)
            """,
            (batch_id, len(payload.invoices), now, now),
        )

        for position, raw_invoice_payload in enumerate(payload.invoices):
            savepoint = f"invoice_batch_item_{position}"
            invoice_number = _batch_item_invoice_number(raw_invoice_payload, position)
            db.execute(f"SAVEPOINT {savepoint}")
            try:
                invoice_payload = InvoiceCreate.model_validate(raw_invoice_payload)
                row, upsert_status, classification_status = _upsert_invoice_in_db(
                    db,
                    invoice_payload,
                )
                db.execute(
                    """
                    INSERT INTO invoice_batch_items (
                        id, batch_id, position, invoice_id, invoice_version,
                        invoice_number, upsert_status, classification_status,
                        error, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        batch_id,
                        position,
                        row["id"],
                        row["version"],
                        invoice_payload.invoice_number,
                        upsert_status,
                        classification_status,
                        now,
                        now,
                    ),
                )
                db.execute(f"RELEASE SAVEPOINT {savepoint}")
            except Exception as exc:
                db.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                db.execute(f"RELEASE SAVEPOINT {savepoint}")
                db.execute(
                    """
                    INSERT INTO invoice_batch_items (
                        id, batch_id, position, invoice_number, upsert_status,
                        classification_status, error, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, 'failed', 'failed', ?, ?, ?)
                    """,
                    (
                        str(uuid.uuid4()),
                        batch_id,
                        position,
                        invoice_number,
                        str(exc)[:2000] or exc.__class__.__name__,
                        now,
                        now,
                    ),
                )

        _refresh_batch_record(db, batch_id)
        response = _batch_response(db, batch_id)

    if response is None:  # pragma: no cover - protected by the transaction above
        raise RuntimeError("Invoice batch disappeared after creation")
    return response


def _batch_item_invoice_number(raw_payload: object, position: int) -> str:
    if isinstance(raw_payload, InvoiceCreate):
        return raw_payload.invoice_number
    if isinstance(raw_payload, dict):
        value = raw_payload.get("invoice_number")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return f"item-{position + 1}"


def get_invoice_batch(batch_id: str) -> InvoiceBatchResponse | None:
    with db_session() as db:
        if not db.execute(
            "SELECT 1 FROM invoice_batches WHERE id = ?",
            (batch_id,),
        ).fetchone():
            return None
        _sync_batch_item_statuses(db, batch_id)
        _refresh_batch_record(db, batch_id)
        return _batch_response(db, batch_id)


def _complete_batch_items_for_classification(
    db: sqlite3.Connection,
    invoice_id: str,
    invoice_version: int,
) -> None:
    batch_rows = db.execute(
        """
        SELECT DISTINCT batch_id FROM invoice_batch_items
        WHERE invoice_id = ? AND invoice_version = ?
        """,
        (invoice_id, invoice_version),
    ).fetchall()
    db.execute(
        """
        UPDATE invoice_batch_items
        SET classification_status = 'completed', error = NULL, updated_at = ?
        WHERE invoice_id = ? AND invoice_version = ?
        """,
        (utc_now(), invoice_id, invoice_version),
    )
    for row in batch_rows:
        _refresh_batch_record(db, row["batch_id"])


def save_classification(
    invoice_id: str,
    classification: InvoiceClassification,
    model_name: str,
    prompt_version: str = "classification_v1",
) -> InvoiceClassification:
    classification_id = str(uuid.uuid4())
    now = utc_now()
    with db_session() as db:
        expected = _expected_classification_version.get()
        if expected and expected[0] == invoice_id:
            invoice_version = expected[1]
        else:
            invoice_row = db.execute(
                "SELECT version FROM invoices WHERE id = ?",
                (invoice_id,),
            ).fetchone()
            if not invoice_row:
                raise ValueError("Invoice not found")
            invoice_version = int(invoice_row["version"])

        if expected and expected[0] == invoice_id and expected[2]:
            claimed = db.execute(
                """
                UPDATE classification_jobs
                SET status = 'completed', completed_at = ?, updated_at = ?,
                    lease_expires_at = NULL, last_error = NULL
                WHERE id = ? AND invoice_id = ? AND invoice_version = ?
                  AND worker_id = ? AND attempts = ? AND status = 'running'
                """,
                (
                    now,
                    now,
                    expected[2],
                    invoice_id,
                    invoice_version,
                    expected[3],
                    expected[4],
                ),
            )
            if claimed.rowcount != 1:
                raise RuntimeError(
                    "Classification result belongs to an expired worker lease."
                )

        db.execute(
            """
            INSERT INTO invoice_classifications (
                id, invoice_id, invoice_version, category, status, confidence,
                reason, evidence_json, model_name, prompt_version,
                created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                classification_id,
                invoice_id,
                invoice_version,
                classification.category,
                classification.status,
                classification.confidence,
                classification.reason,
                encode_json([item.model_dump() for item in classification.evidence]),
                model_name,
                prompt_version,
                now,
                now,
            ),
        )
        if not expected or expected[0] != invoice_id or not expected[2]:
            db.execute(
                """
                UPDATE classification_jobs
                SET status = 'completed', completed_at = ?, updated_at = ?,
                    lease_expires_at = NULL, last_error = NULL
                WHERE invoice_id = ? AND invoice_version = ?
                  AND status IN ('queued', 'running')
                """,
                (now, now, invoice_id, invoice_version),
            )
        _complete_batch_items_for_classification(db, invoice_id, invoice_version)
    return classification


def review_classification(
    invoice_id: str,
    payload: ReviewClassificationRequest,
) -> InvoiceClassification:
    invoice = get_invoice(invoice_id)
    if not invoice:
        raise ValueError("Invoice not found")

    previous = invoice.classification
    reason = payload.reason or (
        previous.reason if previous else "Human reviewed the ESG category."
    )
    evidence = previous.evidence if previous else []
    classification = InvoiceClassification(
        category=payload.category,
        status="Reviewed",
        confidence=previous.confidence if previous else "medium",
        reason=reason,
        evidence=evidence,
    )
    return save_classification(
        invoice_id=invoice_id,
        classification=classification,
        model_name="human-review",
        prompt_version="human_review_v1",
    )


def list_unprocessed_invoice_ids() -> list[str]:
    with db_session() as db:
        rows = db.execute(
            """
            SELECT i.id
            FROM invoices i
            WHERE NOT EXISTS (
                SELECT 1 FROM invoice_classifications c
                WHERE c.invoice_id = i.id AND c.invoice_version = i.version
            )
            ORDER BY i.created_at ASC
            """
        ).fetchall()
    return [row["id"] for row in rows]


def delete_invoice(invoice_id: str) -> bool:
    with db_session() as db:
        batch_rows = db.execute(
            "SELECT DISTINCT batch_id FROM invoice_batch_items WHERE invoice_id = ?",
            (invoice_id,),
        ).fetchall()
        db.execute(
            """
            UPDATE invoice_batch_items
            SET classification_status = 'failed',
                error = 'Invoice was deleted.', updated_at = ?
            WHERE invoice_id = ?
            """,
            (utc_now(), invoice_id),
        )
        cursor = db.execute("DELETE FROM invoices WHERE id = ?", (invoice_id,))
        for row in batch_rows:
            _refresh_batch_record(db, row["batch_id"])
    return cursor.rowcount > 0
