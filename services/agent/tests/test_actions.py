from unittest.mock import AsyncMock, Mock

import pytest
from app.core.actions import action_confirmation
from app.core.models import Critique, LangChainSupportModel
from app.tools.registry import ToolResult


def accepted(action="refund", **arguments):
    return {"action": action, "result": "accepted", "arguments": arguments}


@pytest.mark.parametrize(
    ("action", "output", "expected"),
    [
        (
            "refund",
            accepted(amount=17.5, order_id="confirmed-order"),
            "Your $17.50 refund request for order confirmed-order has been approved and submitted.",
        ),
        (
            "account_credit",
            accepted("account_credit", amount=12),
            "Your $12.00 account credit has been approved and submitted.",
        ),
        (
            "account_credit",
            accepted("account_credit"),
            "Your account credit has been approved and submitted.",
        ),
        (
            "cancel_order",
            accepted("cancel_order", order_id="order-7"),
            "Your cancellation request for order order-7 has been approved and submitted.",
        ),
        (
            "cancel_order",
            accepted("cancel_order"),
            "Your order cancellation has been approved and submitted.",
        ),
    ],
)
def test_confirmation_uses_only_accepted_adapter_facts(action, output, expected):
    assert action_confirmation(action, output) == expected


@pytest.mark.parametrize(
    "output",
    [
        None,
        {},
        "accepted",
        {"action": "refund", "result": "accepted"},
        {**accepted(amount=25, order_id="demo-1"), "result": "failed"},
        {**accepted(amount=25, order_id="demo-1"), "result": "pending"},
        accepted("account_credit", amount=25),
        accepted(order_id="demo-1"),
        accepted(amount=25),
        accepted(amount=25, order_id=""),
        accepted(amount=25, order_id="   "),
        accepted(amount=0, order_id="demo-1"),
        accepted(amount=-1, order_id="demo-1"),
        accepted(amount=float("nan"), order_id="demo-1"),
        accepted(amount=float("inf"), order_id="demo-1"),
        accepted(amount=True, order_id="demo-1"),
        accepted(amount="25", order_id="demo-1"),
    ],
)
def test_malformed_unaccepted_or_wrong_action_cannot_produce_confirmation(output):
    with pytest.raises(ValueError):
        action_confirmation("refund", output)


async def test_apply_action_uses_result_amount_and_preserves_only_draft_citations(nodes):
    raw_draft = (
        "Would you like me to submit the $999 refund for review? [lookup_order] [refund-policy]"
    )
    state = {
        "ticket_id": "t",
        "customer_id": "c",
        "order_id": "demo-1",
        "plan": {"action": "refund", "rationale": "requested", "amount": 25},
        "approval": {"decision": "approve", "review_id": "t:business_action"},
        "refund_evidence": {"eligible": True},
        "draft": raw_draft,
    }
    nodes._tools.execute_action.return_value = ToolResult(
        name="refund",
        output=accepted(amount=17.5, order_id="demo-1"),
    )
    result = await nodes.apply_action(state)
    assert result["final_answer"] == (
        "Your $17.50 refund request for order demo-1 has been approved and submitted."
    )
    assert result["status"] == "completed"
    assert result["citations"] == ["lookup_order", "refund-policy"]
    assert state["draft"] == raw_draft
    nodes._model.draft.assert_not_awaited()


async def test_wrong_tool_name_cannot_produce_confirmation(nodes):
    nodes._tools.execute_action.return_value = ToolResult(
        name="cancel_order",
        output=accepted(amount=25, order_id="demo-1"),
    )
    with pytest.raises(ValueError, match="unexpected action"):
        await nodes.apply_action(
            {
                "ticket_id": "t",
                "customer_id": "c",
                "order_id": "demo-1",
                "plan": {"action": "refund", "rationale": "requested", "amount": 25},
                "approval": {"decision": "approve", "review_id": "t:business_action"},
                "refund_evidence": {"eligible": True},
                "draft": "[refund-policy]",
            }
        )


async def test_live_critique_instructions_cover_redundant_consent_and_premature_success():
    model = LangChainSupportModel.__new__(LangChainSupportModel)
    model._model = Mock()
    structured = model._model.with_structured_output.return_value
    structured.ainvoke = AsyncMock(
        return_value=Critique(passed=False, score=2, feedback="Needs revision")
    )
    await model.critique("Would you like me to submit it?", "Planned action: refund")
    prompt = structured.ainvoke.call_args.args[0]
    assert "no additional customer confirmation is required" in prompt
    assert "Fail refund drafts that ask permission" in prompt
    assert "before execution" in prompt
    assert "Planned action: refund" in prompt
