from datetime import datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, Field

# These models are the Temporal payload contract. The Go gateway mirrors them field
# for field in services/gateway/internal/api/types.go; renaming a field here without
# renaming it there breaks the workflow at runtime, not at build time.


class RunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    WAITING_APPROVAL = "waiting_approval"
    REJECTED = "rejected"


class TicketRequest(BaseModel):
    customer_id: str = Field(min_length=1, max_length=100)
    message: str = Field(min_length=1, max_length=8_000)
    order_id: str | None = Field(default=None, max_length=100)
    channel: Literal["api", "email", "chat"] = "api"
    metadata: dict[str, Any] = Field(default_factory=dict)


class ApprovalDecision(BaseModel):
    review_id: str = Field(min_length=1, max_length=200)
    decision: Literal["approve", "reject"]
    reviewer: str = Field(min_length=1, max_length=100)
    comment: str | None = Field(default=None, max_length=1_000)


class PendingAction(BaseModel):
    review_id: str
    action: str
    arguments: dict[str, Any]
    reason: str


class RunState(BaseModel):
    """The workflow's public state, returned by the `get_state` query and the run result."""

    ticket_id: str
    status: RunStatus
    answer: str | None = None
    category: str | None = None
    pending_action: PendingAction | None = None
    citations: list[str] = Field(default_factory=list)
    safety_flags: list[str] = Field(default_factory=list)
    # Set from workflow.now(), never datetime.now(): workflow code must be deterministic
    # so that replay produces the same value.
    created_at: datetime | None = None


class KnowledgeDocument(BaseModel):
    id: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=300)
    content: str = Field(min_length=1, max_length=50_000)
    source: str = Field(min_length=1, max_length=500)
    metadata: dict[str, Any] = Field(default_factory=dict)
