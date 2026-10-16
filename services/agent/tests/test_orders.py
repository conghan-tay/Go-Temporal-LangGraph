import json
from datetime import date, timedelta

import pytest
from app.tools.orders import demo_order, normalize_order, refund_evidence
from app.tools.registry import ToolRegistry, lookup_order

REFERENCE = date(2026, 10, 16)


def order(age=10, status="delivered"):
    return {
        "order_id": "order-1",
        "status": status,
        "eta_business_days": 0,
        "delivered_at": (REFERENCE - timedelta(days=age)).isoformat(),
        "refundable_amount": 25,
    }


@pytest.mark.parametrize(("age", "eligible"), [(0, True), (30, True), (31, False), (-1, False)])
def test_refund_boundary(age, eligible):
    evidence = refund_evidence(order(age), REFERENCE.isoformat())
    assert evidence["eligible"] is eligible
    assert evidence["request_date"] == REFERENCE.isoformat()
    assert evidence["amount"] == 25


@pytest.mark.parametrize("status", ["processing", "in_transit", "delivered"])
def test_missing_delivery_date_never_eligible(status):
    data = order(status=status)
    data["delivered_at"] = None
    assert not refund_evidence(data, REFERENCE.isoformat())["eligible"]


def test_demo_repeatability_and_date_range():
    for index in range(100):
        first = demo_order(f"demo-{index}", REFERENCE.isoformat())
        assert first == demo_order(f"demo-{index}", REFERENCE.isoformat())
        if first["status"] == "delivered":
            assert 0 <= (REFERENCE - date.fromisoformat(first["delivered_at"])).days <= 60
        else:
            assert first["delivered_at"] is None


@pytest.mark.parametrize("shape", ["dict", "string", "text", "content", "structured"])
def test_normalize_mcp_results(shape):
    data = order()
    text = [{"type": "text", "text": json.dumps(data)}]
    output = {
        "dict": data,
        "string": json.dumps(data),
        "text": text,
        "content": {"content": text},
        "structured": {"structuredContent": data},
    }[shape]
    assert normalize_order(output, "order-1")["refundable_amount"] == 25


@pytest.mark.parametrize(
    "output",
    [
        {"isError": True, "content": []},
        [],
        {**order(), "delivered_at": "yesterday"},
        {**order(), "refundable_amount": float("nan")},
        {**order(), "status": "invented"},
        {**order(), "order_id": "different"},
        {"status": "delivered"},
    ],
)
def test_invalid_order_rejected(output):
    with pytest.raises(ValueError):
        normalize_order(output, "order-1")


async def test_local_tool_matches_shared_contract():
    result = await ToolRegistry([lookup_order]).execute_read_tool(
        "lookup_order", {"order_id": "demo-1", "as_of_date": REFERENCE.isoformat()}
    )
    assert result.output == demo_order("demo-1", REFERENCE.isoformat())
