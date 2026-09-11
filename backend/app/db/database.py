import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping

from app.core.config import settings


SQLITE_BUSY_TIMEOUT_MS = 10_000


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def encode_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def decode_json(value: str | None, default: Any) -> Any:
    if not value:
        return default
    return json.loads(value)


def get_connection() -> sqlite3.Connection:
    Path(settings.database_path).parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(
        settings.database_path,
        timeout=SQLITE_BUSY_TIMEOUT_MS / 1000,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA synchronous = NORMAL")
    return connection


@contextmanager
def db_session() -> Iterator[sqlite3.Connection]:
    connection = get_connection()
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _column_names(db: sqlite3.Connection, table: str) -> set[str]:
    return {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}


def _ensure_column(
    db: sqlite3.Connection,
    table: str,
    column: str,
    definition: str,
) -> bool:
    if column in _column_names(db, table):
        return False
    db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")
    return True


def _invoice_content_hash(row: Mapping[str, Any]) -> str:
    canonical = {
        "invoice_number": row["invoice_number"],
        "supplier_name": row["supplier_name"],
        "buyer_name": row["buyer_name"],
        "invoice_date": row["invoice_date"],
        "amount": float(row["amount"]),
        "currency": row["currency"],
        "description": row["description"],
    }
    payload = json.dumps(
        canonical,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _backfill_invoice_metadata(db: sqlite3.Connection) -> None:
    rows = db.execute(
        """
        SELECT id, invoice_number, supplier_name, buyer_name, invoice_date,
               amount, currency, description, created_at, content_hash, updated_at
        FROM invoices
        """
    ).fetchall()
    for row in rows:
        content_hash = row["content_hash"] or _invoice_content_hash(row)
        updated_at = row["updated_at"] or row["created_at"] or utc_now()
        db.execute(
            """
            UPDATE invoices
            SET content_hash = ?, updated_at = ?
            WHERE id = ?
            """,
            (content_hash, updated_at, row["id"]),
        )


def init_db() -> None:
    """Create the current schema and migrate databases created by older builds.

    Migrations are intentionally additive so an existing local database can be
    opened without a separate migration command.
    """
    with db_session() as db:
        db.executescript(
            """
            CREATE TABLE IF NOT EXISTS invoices (
                id TEXT PRIMARY KEY,
                invoice_number TEXT NOT NULL UNIQUE,
                supplier_name TEXT NOT NULL,
                buyer_name TEXT,
                invoice_date TEXT NOT NULL,
                amount REAL NOT NULL,
                currency TEXT NOT NULL,
                description TEXT NOT NULL,
                version INTEGER NOT NULL DEFAULT 1,
                content_hash TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS invoice_classifications (
                id TEXT PRIMARY KEY,
                invoice_id TEXT NOT NULL,
                invoice_version INTEGER NOT NULL DEFAULT 1,
                category TEXT NOT NULL,
                status TEXT NOT NULL,
                confidence TEXT NOT NULL,
                reason TEXT NOT NULL,
                evidence_json TEXT NOT NULL,
                model_name TEXT NOT NULL,
                prompt_version TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(invoice_id) REFERENCES invoices(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS reports (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                period_start TEXT NOT NULL,
                period_end TEXT NOT NULL,
                status TEXT NOT NULL,
                content TEXT NOT NULL,
                model_name TEXT NOT NULL,
                source_run_id TEXT,
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS report_versions (
                id TEXT PRIMARY KEY,
                report_id TEXT NOT NULL,
                version_number INTEGER NOT NULL,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(report_id) REFERENCES reports(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS report_evidence_register_items (
                id TEXT PRIMARY KEY,
                report_id TEXT NOT NULL,
                group_id TEXT NOT NULL,
                group_name TEXT NOT NULL,
                invoice_id TEXT NOT NULL,
                invoice_number TEXT NOT NULL,
                invoice_version INTEGER NOT NULL DEFAULT 1,
                supplier_name TEXT NOT NULL,
                invoice_date TEXT NOT NULL,
                description TEXT NOT NULL,
                amount REAL NOT NULL,
                currency TEXT NOT NULL,
                classification_category TEXT NOT NULL,
                classification_status TEXT NOT NULL DEFAULT 'Reviewed',
                classification_reason TEXT NOT NULL,
                guideline_source TEXT,
                guideline_section TEXT,
                guideline_topic TEXT,
                guideline_page TEXT,
                guideline_chunk_id TEXT,
                created_at TEXT NOT NULL,
                FOREIGN KEY(report_id) REFERENCES reports(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS report_guideline_citations (
                id TEXT PRIMARY KEY,
                report_id TEXT NOT NULL,
                occurrence_index INTEGER NOT NULL,
                claim_text TEXT NOT NULL,
                chunk_id TEXT NOT NULL,
                source TEXT NOT NULL,
                topic TEXT NOT NULL,
                section TEXT NOT NULL,
                page TEXT NOT NULL,
                source_version TEXT NOT NULL DEFAULT '',
                index_version TEXT NOT NULL DEFAULT '',
                supporting_text TEXT NOT NULL,
                validation_status TEXT NOT NULL,
                created_at TEXT NOT NULL,
                FOREIGN KEY(report_id) REFERENCES reports(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS invoice_batches (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                total_count INTEGER NOT NULL,
                created_count INTEGER NOT NULL DEFAULT 0,
                updated_count INTEGER NOT NULL DEFAULT 0,
                unchanged_count INTEGER NOT NULL DEFAULT 0,
                failed_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS invoice_batch_items (
                id TEXT PRIMARY KEY,
                batch_id TEXT NOT NULL,
                position INTEGER NOT NULL,
                invoice_id TEXT,
                invoice_version INTEGER,
                invoice_number TEXT NOT NULL,
                upsert_status TEXT NOT NULL,
                classification_status TEXT NOT NULL,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(batch_id, position),
                FOREIGN KEY(batch_id) REFERENCES invoice_batches(id) ON DELETE CASCADE,
                FOREIGN KEY(invoice_id) REFERENCES invoices(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS classification_jobs (
                id TEXT PRIMARY KEY,
                invoice_id TEXT NOT NULL,
                invoice_version INTEGER NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL DEFAULT 3,
                available_at TEXT NOT NULL,
                worker_id TEXT,
                lease_expires_at TEXT,
                last_error TEXT,
                created_at TEXT NOT NULL,
                started_at TEXT,
                completed_at TEXT,
                updated_at TEXT NOT NULL,
                UNIQUE(invoice_id, invoice_version),
                FOREIGN KEY(invoice_id) REFERENCES invoices(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS report_agent_runs (
                id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                request_json TEXT NOT NULL,
                report_id TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(report_id) REFERENCES reports(id) ON DELETE SET NULL
            );

            CREATE TABLE IF NOT EXISTS report_agent_steps (
                id TEXT NOT NULL,
                run_id TEXT NOT NULL,
                label TEXT NOT NULL,
                status TEXT NOT NULL,
                detail TEXT,
                sequence INTEGER NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(run_id, id),
                FOREIGN KEY(run_id) REFERENCES report_agent_runs(id) ON DELETE CASCADE
            );
            """
        )

        _ensure_column(db, "invoices", "version", "INTEGER NOT NULL DEFAULT 1")
        _ensure_column(db, "invoices", "content_hash", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(db, "invoices", "updated_at", "TEXT")
        classification_version_added = _ensure_column(
            db,
            "invoice_classifications",
            "invoice_version",
            "INTEGER NOT NULL DEFAULT 1",
        )
        _ensure_column(db, "reports", "source_run_id", "TEXT")
        _ensure_column(
            db,
            "report_evidence_register_items",
            "invoice_version",
            "INTEGER NOT NULL DEFAULT 1",
        )
        _ensure_column(
            db,
            "report_evidence_register_items",
            "classification_status",
            "TEXT NOT NULL DEFAULT 'Reviewed'",
        )
        _ensure_column(
            db,
            "report_guideline_citations",
            "supporting_text",
            "TEXT NOT NULL DEFAULT ''",
        )

        _backfill_invoice_metadata(db)
        if classification_version_added:
            db.execute(
                """
                UPDATE invoice_classifications
                SET invoice_version = COALESCE(
                    (SELECT version FROM invoices
                     WHERE invoices.id = invoice_classifications.invoice_id),
                    1
                )
                """
            )

        db.executescript(
            """
            CREATE INDEX IF NOT EXISTS idx_invoices_date
                ON invoices(invoice_date);
            CREATE INDEX IF NOT EXISTS idx_invoices_updated_at
                ON invoices(updated_at);
            CREATE INDEX IF NOT EXISTS idx_invoice_classifications_current
                ON invoice_classifications(invoice_id, invoice_version, created_at DESC);
            CREATE INDEX IF NOT EXISTS idx_invoice_batch_items_batch
                ON invoice_batch_items(batch_id, position);
            CREATE INDEX IF NOT EXISTS idx_invoice_batch_items_invoice_version
                ON invoice_batch_items(invoice_id, invoice_version);
            CREATE INDEX IF NOT EXISTS idx_classification_jobs_claim
                ON classification_jobs(status, available_at, created_at);
            CREATE INDEX IF NOT EXISTS idx_classification_jobs_lease
                ON classification_jobs(status, lease_expires_at);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_reports_source_run_id
                ON reports(source_run_id);
            CREATE INDEX IF NOT EXISTS idx_report_evidence_register_report_group
                ON report_evidence_register_items(report_id, group_id, invoice_date);
            CREATE INDEX IF NOT EXISTS idx_report_guideline_citations_report
                ON report_guideline_citations(report_id, occurrence_index);
            CREATE INDEX IF NOT EXISTS idx_report_agent_runs_status
                ON report_agent_runs(status, updated_at);
            CREATE INDEX IF NOT EXISTS idx_report_agent_steps_run_sequence
                ON report_agent_steps(run_id, sequence);
            """
        )
