"""Validated order evidence and repeatable demo data shared with the MCP service."""

import hashlib
import json
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class OrderSnapshot(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    order_id: str
    status: Literal["processing", "in_transit", "delivered"]
    eta_business_days: int = Field(ge=0)
    delivered_at: date | None
    refundable_amount: float = Field(gt=0, le=10_000)


def demo_order(order_id: str, as_of_date: str | None = None) -> dict[str, Any]:
    """Demo dates are relative to a supplied request date, not real order records.

    Explicit reference dates make retries/replay and manual examples repeatable.
    The optional default preserves standalone MCP calls with only an order ID.
    """
    reference = date.fromisoformat(as_of_date) if as_of_date else datetime.now(UTC).date()
    index = sum(ord(character) for character in order_id) % 3
    status, eta = (("processing", 5), ("in_transit", 3), ("delivered", 0))[index]
    age = int.from_bytes(hashlib.sha256(order_id.encode()).digest(), "big") % 61
    return {
        "order_id": order_id,
        "status": status,
        "eta_business_days": eta,
        "delivered_at": (reference - timedelta(days=age)).isoformat()
        if status == "delivered"
        else None,
        "refundable_amount": 25.0,
    }


def normalize_order(output: Any, order_id: str) -> dict[str, Any]:
    """Accept structured MCP results or its JSON text content, then validate."""
    if isinstance(output, dict) and output.get("isError"):
        raise ValueError("Order lookup reported an error")
    if isinstance(output, dict) and "structuredContent" in output:
        output = output["structuredContent"]
    elif isinstance(output, dict) and "content" in output:
        output = output["content"]
    if isinstance(output, list):
        texts = [
            item["text"] for item in output if isinstance(item, dict) and item.get("type") == "text"
        ]
        if len(texts) != 1:
            raise ValueError("Expected one JSON order result")
        output = json.loads(texts[0])
    if isinstance(output, str):
        output = json.loads(output)
    order = OrderSnapshot.model_validate(output)
    if order.order_id != order_id:
        raise ValueError("Order lookup returned a different order")
    return order.model_dump(mode="json")


def refund_evidence(order: dict[str, Any], request_date: str) -> dict[str, Any]:
    """Compute policy in code; invalid or missing evidence never authorizes a refund."""
    snapshot = OrderSnapshot.model_validate(order)
    reference = date.fromisoformat(request_date)
    delivery = snapshot.delivered_at
    evidence = {
        "request_date": reference.isoformat(),
        "delivered_at": delivery.isoformat() if delivery else None,
        "amount": snapshot.refundable_amount,
        "eligible": False,
        "reason": "The order has not been delivered; a delivery-window refund cannot be verified.",
    }
    if snapshot.status != "delivered":
        return evidence
    if delivery is None or delivery > reference:
        evidence["reason"] = (
            "The order has no valid delivery date; refund eligibility cannot be verified."
        )
        return evidence
    age = (reference - delivery).days
    evidence.update(days_since_delivery=age, eligible=age <= 30)
    evidence["reason"] = (
        f"Delivered {delivery.isoformat()}; request received {request_date} ({age} days later). "
        + ("Within the 30-day refund window." if age <= 30 else "Outside the 30-day refund window.")
    )
    return evidence
