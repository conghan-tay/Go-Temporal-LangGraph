"""Validate accepted business-action results before announcing submission."""

from typing import Any, Literal

from pydantic import BaseModel, Field


class ActionArguments(BaseModel):
    order_id: str | None = Field(default=None, min_length=1, max_length=100)
    amount: float | None = Field(default=None, strict=True, ge=0, le=10_000, allow_inf_nan=False)


class AcceptedActionResult(BaseModel):
    action: Literal["refund", "account_credit", "cancel_order"]
    result: Literal["accepted"]
    arguments: ActionArguments


def action_confirmation(expected_action: str, output: Any) -> str:
    """Use adapter-confirmed facts, never the pre-approval draft or planned amount.

    Accepted means submission was accepted, not that money reached the customer.
    Validation failures propagate through the existing activity retry/error policy.
    """
    result = AcceptedActionResult.model_validate(output)
    if result.action != expected_action:
        raise ValueError("Business-action result does not match the expected action")
    amount = result.arguments.amount
    order_id = result.arguments.order_id
    if result.action == "refund":
        if amount is None or amount <= 0 or not order_id or not order_id.strip():
            raise ValueError("Accepted refund result requires a positive amount and order ID")
        return (
            f"Your ${amount:.2f} refund request for order {order_id} "
            "has been approved and submitted."
        )
    if result.action == "account_credit":
        credit = f"${amount:.2f} account credit" if amount is not None else "account credit"
        return f"Your {credit} has been approved and submitted."
    cancellation = (
        f"cancellation request for order {order_id}" if order_id else "order cancellation"
    )
    return f"Your {cancellation} has been approved and submitted."
