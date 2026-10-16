import json
import os
import subprocess
import sys
from pathlib import Path


def test_temporal_extra_fields_and_structlog_are_json():
    # Keep global logging configuration isolated from pytest's capture handlers.
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            """
import logging
import structlog
from app.core.logging import configure_logging
configure_logging()
logging.getLogger("temporalio.workflow").info("review_waiting", extra={
    "ticket_id":"ticket-1", "review_id":"ticket-1:input_review", "stage":"input_review",
    "safety_flags":["prompt_injection:role_impersonation"],
})
structlog.get_logger().info("order_lookup_unavailable", ticket_id="ticket-1")
""",
        ],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).resolve().parents[1])},
        capture_output=True,
        text=True,
        check=True,
    )
    records = [json.loads(line) for line in result.stdout.splitlines()]
    assert records[0]["safety_flags"] == ["prompt_injection:role_impersonation"]
    assert records[0]["review_id"] == "ticket-1:input_review"
    assert records[0]["stage"] == "input_review"
    assert records[1]["event"] == "order_lookup_unavailable"
