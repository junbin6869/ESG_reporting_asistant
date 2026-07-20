import json
import os
import socket
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from typing import Callable

from app.core.config import settings
from app.db.database import db_session, get_connection, utc_now


MAX_ATTEMPTS = 3
LEASE_SECONDS = 120
HEARTBEAT_SECONDS = 30
POLL_SECONDS = 1.0

_worker_threads: list[threading.Thread] = []
_worker_stop = threading.Event()
_worker_lock = threading.Lock()


def _future_timestamp(seconds: float) -> str:
    return (datetime.now(timezone.utc) + timedelta(seconds=seconds)).isoformat()


def _error_message(exc: Exception) -> str:
    detail = getattr(exc, "detail", None)
    if detail is not None:
        if isinstance(detail, str):
            message = detail
        else:
            message = json.dumps(detail, ensure_ascii=False)
    else:
        message = str(exc) or exc.__class__.__name__
    return message[:2000]


def enqueue_classification_job(
    db: sqlite3.Connection,
    invoice_id: str,
    invoice_version: int,
) -> str:
    """Idempotently enqueue an invoice version inside the caller's transaction."""
    now = utc_now()
    db.execute(
        """
        INSERT INTO classification_jobs (
            id, invoice_id, invoice_version, status, attempts, max_attempts,
            available_at, created_at, updated_at
        ) VALUES (?, ?, ?, 'queued', 0, ?, ?, ?, ?)
        ON CONFLICT(invoice_id, invoice_version) DO UPDATE SET
            status = 'queued', attempts = 0, max_attempts = excluded.max_attempts,
            available_at = excluded.available_at, worker_id = NULL,
            lease_expires_at = NULL, started_at = NULL, completed_at = NULL,
            last_error = NULL, updated_at = excluded.updated_at
        WHERE classification_jobs.status = 'failed'
        """,
        (
            str(uuid.uuid4()),
            invoice_id,
            invoice_version,
            MAX_ATTEMPTS,
            now,
            now,
            now,
        ),
    )
    row = db.execute(
        """
        SELECT status FROM classification_jobs
        WHERE invoice_id = ? AND invoice_version = ?
        """,
        (invoice_id, invoice_version),
    ).fetchone()
    if not row:  # pragma: no cover - INSERT/SELECT are in the same transaction
        raise RuntimeError("Unable to enqueue classification job")
    return row["status"]


def enqueue_unclassified_invoices() -> int:
    """Create missing jobs for legacy or interrupted invoice imports."""
    enqueued = 0
    with db_session() as db:
        rows = db.execute(
            """
            SELECT i.id, i.version
            FROM invoices i
            WHERE NOT EXISTS (
                SELECT 1 FROM invoice_classifications c
                WHERE c.invoice_id = i.id AND c.invoice_version = i.version
            )
              AND NOT EXISTS (
                SELECT 1 FROM classification_jobs j
                WHERE j.invoice_id = i.id AND j.invoice_version = i.version
                  AND j.status != 'failed'
            )
            ORDER BY i.created_at
            """
        ).fetchall()
        for row in rows:
            enqueue_classification_job(db, row["id"], int(row["version"]))
            enqueued += 1
    return enqueued


def _refresh_affected_batches(
    db: sqlite3.Connection,
    invoice_id: str,
    invoice_version: int,
    status: str,
    error: str | None,
) -> None:
    from app.services.invoice_service import _refresh_batch_record

    batch_rows = db.execute(
        """
        SELECT DISTINCT batch_id
        FROM invoice_batch_items
        WHERE invoice_id = ? AND invoice_version = ?
        """,
        (invoice_id, invoice_version),
    ).fetchall()
    db.execute(
        """
        UPDATE invoice_batch_items
        SET classification_status = ?, error = ?, updated_at = ?
        WHERE invoice_id = ? AND invoice_version = ?
          AND upsert_status != 'failed'
        """,
        (status, error, utc_now(), invoice_id, invoice_version),
    )
    for row in batch_rows:
        _refresh_batch_record(db, row["batch_id"])


def claim_next_classification_job(worker_id: str | None = None) -> dict | None:
    """Atomically lease one queued job across threads and OS processes."""
    worker_id = worker_id or _default_worker_id()
    now = utc_now()
    lease_expires_at = _future_timestamp(LEASE_SECONDS)
    connection = get_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """
            SELECT *
            FROM classification_jobs
            WHERE status = 'queued' AND available_at <= ?
            ORDER BY available_at, created_at, id
            LIMIT 1
            """,
            (now,),
        ).fetchone()
        if not row:
            connection.commit()
            return None

        cursor = connection.execute(
            """
            UPDATE classification_jobs
            SET status = 'running', attempts = attempts + 1,
                worker_id = ?, lease_expires_at = ?, started_at = ?,
                updated_at = ?
            WHERE id = ? AND status = 'queued'
            """,
            (worker_id, lease_expires_at, now, now, row["id"]),
        )
        if cursor.rowcount != 1:  # defensive; BEGIN IMMEDIATE already serializes claims
            connection.rollback()
            return None

        claimed = connection.execute(
            "SELECT * FROM classification_jobs WHERE id = ?",
            (row["id"],),
        ).fetchone()
        _refresh_affected_batches(
            connection,
            claimed["invoice_id"],
            int(claimed["invoice_version"]),
            "running",
            None,
        )
        connection.commit()
        return dict(claimed)
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _heartbeat(job_id: str, worker_id: str, stop: threading.Event) -> None:
    while not stop.wait(HEARTBEAT_SECONDS):
        try:
            with db_session() as db:
                db.execute(
                    """
                    UPDATE classification_jobs
                    SET lease_expires_at = ?, updated_at = ?
                    WHERE id = ? AND worker_id = ? AND status = 'running'
                    """,
                    (_future_timestamp(LEASE_SECONDS), utc_now(), job_id, worker_id),
                )
        except Exception:
            # Losing one heartbeat is safe; the lease still has ample headroom.
            pass


def _finish_job(
    job: dict,
    *,
    succeeded: bool,
    error: str | None = None,
    retry_delay_seconds: float = 1.0,
) -> None:
    now = utc_now()
    with db_session() as db:
        current = db.execute(
            """
            SELECT status, attempts, max_attempts, worker_id
            FROM classification_jobs WHERE id = ?
            """,
            (job["id"],),
        ).fetchone()
        if not current:
            return
        if (
            current["worker_id"] != job["worker_id"]
            or int(current["attempts"]) != int(job["attempts"])
        ):
            return

        if succeeded:
            cursor = db.execute(
                """
                UPDATE classification_jobs
                SET status = 'completed', completed_at = COALESCE(completed_at, ?),
                    updated_at = ?, lease_expires_at = NULL, last_error = NULL
                WHERE id = ? AND worker_id = ?
                  AND attempts = ?
                  AND status IN ('running', 'completed')
                """,
                (now, now, job["id"], job["worker_id"], job["attempts"]),
            )
            if cursor.rowcount != 1:
                return
            _refresh_affected_batches(
                db,
                job["invoice_id"],
                int(job["invoice_version"]),
                "completed",
                None,
            )
            return

        attempts = int(current["attempts"])
        max_attempts = int(current["max_attempts"])
        if attempts < max_attempts:
            status = "queued"
            available_at = _future_timestamp(retry_delay_seconds * (2 ** (attempts - 1)))
            cursor = db.execute(
                """
                UPDATE classification_jobs
                SET status = 'queued', available_at = ?, worker_id = NULL,
                    lease_expires_at = NULL, last_error = ?, updated_at = ?
                WHERE id = ? AND worker_id = ? AND attempts = ?
                  AND status = 'running'
                """,
                (
                    available_at,
                    error,
                    now,
                    job["id"],
                    job["worker_id"],
                    job["attempts"],
                ),
            )
        else:
            status = "failed"
            cursor = db.execute(
                """
                UPDATE classification_jobs
                SET status = 'failed', completed_at = ?, lease_expires_at = NULL,
                    last_error = ?, updated_at = ?
                WHERE id = ? AND worker_id = ? AND attempts = ?
                  AND status = 'running'
                """,
                (
                    now,
                    error,
                    now,
                    job["id"],
                    job["worker_id"],
                    job["attempts"],
                ),
            )
        if cursor.rowcount != 1:
            return
        _refresh_affected_batches(
            db,
            job["invoice_id"],
            int(job["invoice_version"]),
            status,
            error if status == "failed" else None,
        )


def process_claimed_classification_job(
    job: dict,
    classify_fn: Callable[[str], object] | None = None,
    retry_delay_seconds: float = 1.0,
) -> bool:
    """Run one already-claimed job and persist success or retry state."""
    from app.services.invoice_service import classification_for_invoice_version

    if classify_fn is None:
        from app.services.classification_service import classify_invoice

        classify_fn = classify_invoice

    with db_session() as db:
        invoice = db.execute(
            "SELECT version FROM invoices WHERE id = ?",
            (job["invoice_id"],),
        ).fetchone()
    if not invoice or int(invoice["version"]) != int(job["invoice_version"]):
        _finish_job(job, succeeded=True)
        return True

    heartbeat_stop = threading.Event()
    heartbeat = threading.Thread(
        target=_heartbeat,
        args=(job["id"], job["worker_id"], heartbeat_stop),
        daemon=True,
    )
    heartbeat.start()
    try:
        with classification_for_invoice_version(
            job["invoice_id"],
            int(job["invoice_version"]),
            job_id=job["id"],
            worker_id=job["worker_id"],
            attempt=int(job["attempts"]),
        ):
            classify_fn(job["invoice_id"])
    except Exception as exc:
        _finish_job(
            job,
            succeeded=False,
            error=_error_message(exc),
            retry_delay_seconds=retry_delay_seconds,
        )
        return False
    finally:
        heartbeat_stop.set()
        heartbeat.join(timeout=1)

    _finish_job(job, succeeded=True)
    return True


def run_classification_worker_once(
    classify_fn: Callable[[str], object] | None = None,
    worker_id: str | None = None,
    retry_delay_seconds: float = 1.0,
) -> bool | None:
    """Claim and process one job; return None when the queue is empty."""
    job = claim_next_classification_job(worker_id=worker_id)
    if not job:
        return None
    return process_claimed_classification_job(
        job,
        classify_fn=classify_fn,
        retry_delay_seconds=retry_delay_seconds,
    )


def recover_stale_classification_jobs() -> int:
    """Requeue expired leases without disturbing work owned by live processes."""
    now = utc_now()
    recovered = 0
    connection = get_connection()
    try:
        connection.execute("BEGIN IMMEDIATE")
        rows = connection.execute(
            """
            SELECT * FROM classification_jobs
            WHERE status = 'running'
              AND (lease_expires_at IS NULL OR lease_expires_at <= ?)
            """,
            (now,),
        ).fetchall()
        for row in rows:
            attempts = int(row["attempts"])
            max_attempts = int(row["max_attempts"])
            if attempts < max_attempts:
                status = "queued"
                connection.execute(
                    """
                    UPDATE classification_jobs
                    SET status = 'queued', worker_id = NULL,
                        lease_expires_at = NULL, available_at = ?, updated_at = ?,
                        last_error = 'Worker lease expired; job requeued.'
                    WHERE id = ? AND status = 'running'
                    """,
                    (now, now, row["id"]),
                )
                item_error = None
            else:
                status = "failed"
                connection.execute(
                    """
                    UPDATE classification_jobs
                    SET status = 'failed', lease_expires_at = NULL,
                        completed_at = ?, updated_at = ?,
                        last_error = 'Worker lease expired after maximum retries.'
                    WHERE id = ? AND status = 'running'
                    """,
                    (now, now, row["id"]),
                )
                item_error = "Worker lease expired after maximum retries."
            _refresh_affected_batches(
                connection,
                row["invoice_id"],
                int(row["invoice_version"]),
                status,
                item_error,
            )
            recovered += 1
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
    return recovered


def _default_worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def classification_worker_loop() -> None:
    worker_id = _default_worker_id()
    while not _worker_stop.is_set():
        try:
            recover_stale_classification_jobs()
            result = run_classification_worker_once(worker_id=worker_id)
        except Exception as exc:
            print(f"[WARN] Classification worker error: {_error_message(exc)}")
            result = None
        if result is None:
            _worker_stop.wait(POLL_SECONDS)


def start_classification_worker() -> list[threading.Thread]:
    global _worker_threads
    with _worker_lock:
        active = [thread for thread in _worker_threads if thread.is_alive()]
        if active:
            _worker_threads = active
            return list(_worker_threads)
        _worker_stop.clear()
        _worker_threads = []
        for worker_index in range(settings.classification_worker_count):
            thread = threading.Thread(
                target=classification_worker_loop,
                name=f"invoice-classification-worker-{worker_index + 1}",
                daemon=True,
            )
            thread.start()
            _worker_threads.append(thread)
        return list(_worker_threads)


def stop_classification_worker(timeout: float = 2.0) -> None:
    _worker_stop.set()
    for thread in list(_worker_threads):
        if thread.is_alive():
            thread.join(timeout=timeout)
