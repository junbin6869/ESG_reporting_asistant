import unittest
from unittest.mock import patch

from app.core import mcp_audit


class McpAuditTests(unittest.TestCase):
    def test_logging_failure_does_not_escape(self):
        with patch.object(
            mcp_audit,
            "_configure_logger",
            side_effect=OSError("disk unavailable"),
        ):
            mcp_audit.log_mcp_event(
                "search_esg_guidelines",
                "failed",
                error_type="OSError",
            )


if __name__ == "__main__":
    unittest.main()
