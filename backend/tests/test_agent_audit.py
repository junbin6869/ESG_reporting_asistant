import unittest
from unittest.mock import patch

from app.core import agent_audit


class AgentAuditTests(unittest.TestCase):
    def test_logging_failure_does_not_escape(self):
        with patch.object(
            agent_audit,
            "_configure_logger",
            side_effect=OSError("disk unavailable"),
        ):
            agent_audit.log_agent_event(
                "run-1",
                "agent_decision",
                decision="search_more",
            )


if __name__ == "__main__":
    unittest.main()
