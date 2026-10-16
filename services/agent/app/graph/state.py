from typing import Any, TypedDict


class SupportState(TypedDict, total=False):
    # Immutable request data
    ticket_id: str
    customer_id: str
    message: str
    order_id: str | None
    metadata: dict[str, Any]

    # Intermediate graph data. Keeping raw values makes checkpoints debuggable.
    normalized_message: str
    request_date: str
    input_review: dict[str, Any]
    refund_evidence: dict[str, Any]
    safety_flags: list[str]
    classification: dict[str, Any]
    documents: list[dict[str, Any]]
    plan: dict[str, Any]
    tool_results: list[dict[str, Any]]
    draft: str
    critique: dict[str, Any]
    reflection_count: int

    # Terminal API data
    status: str
    final_answer: str
    citations: list[str]
    approval: dict[str, Any]
    action_result: dict[str, Any]
