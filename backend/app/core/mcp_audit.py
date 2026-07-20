import json
import logging
from logging.handlers import RotatingFileHandler
from threading import Lock
from typing import Any

from app.core.config import BACKEND_ROOT
from app.db.database import utc_now


LOG_PATH = BACKEND_ROOT / "logs" / "mcp_server.jsonl"
MAX_LOG_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 5

_logger = logging.getLogger("mcp_server_audit")
_logger.setLevel(logging.INFO)
_logger.propagate = False
_configure_lock = Lock()


def _configure_logger() -> None:
    if _logger.handlers:
        return
    with _configure_lock:
        if _logger.handlers:
            return
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            LOG_PATH,
            maxBytes=MAX_LOG_BYTES,
            backupCount=LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        _logger.addHandler(handler)


def log_mcp_event(operation: str, status: str, **fields: Any) -> None:
    event = {
        "timestamp": utc_now(),
        "operation": operation,
        "status": status,
        **fields,
    }
    try:
        _configure_logger()
        _logger.info(json.dumps(event, ensure_ascii=False, default=str))
    except Exception:
        # Audit logging must never prevent an MCP operation.
        return
