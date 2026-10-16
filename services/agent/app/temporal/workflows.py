"""The durable support-ticket workflow.

Temporal owns everything that used to need a checkpointer database: each graph step is
persisted in the workflow's event history, the approval pause survives worker restarts
and deploys, and a run can wait days for a reviewer without holding any process open.
"""

from datetime import timedelta
from typing import Any

from temporalio import workflow

# Passthrough imports reuse the modules the worker process already loaded rather than
# re-importing them inside the workflow sandbox, which both speeds up workflow startup
# and avoids the sandbox rejecting LangGraph's own imports. Nothing imported here may
# read the filesystem, the clock, or the environment at call time.
with workflow.unsafe.imports_passed_through():
    from langgraph.checkpoint.memory import InMemorySaver
    from langgraph.types import Command
    from temporalio.contrib.langgraph import graph as temporal_graph

    from ..core.schemas import (
        ApprovalDecision,
        PendingAction,
        RunState,
        RunStatus,
        TicketRequest,
    )
    from .mapping import to_run_state

SUPPORT_GRAPH = "support"

# The Go gateway addresses these by name over gRPC and has no access to this module.
# Renaming any of them is a breaking API change; see internal/tickets/temporal.go.
WORKFLOW_NAME = "SupportTicketWorkflow"
SIGNAL_SUBMIT_DECISION = "submit_decision"
QUERY_GET_STATE = "get_state"


@workflow.defn(name=WORKFLOW_NAME)
class SupportTicketWorkflow:
    def __init__(self) -> None:
        self._state = RunState(ticket_id="", status=RunStatus.RUNNING)
        self._decision: ApprovalDecision | None = None
        self._approval_timeout_hours = 0

    @workflow.query(name=QUERY_GET_STATE)
    def get_state(self) -> RunState:
        """Serve `GET /v1/tickets/{id}`.

        Queries also work against closed workflows, so a completed run answers from
        here too and the gateway needs only one read path.
        """

        return self._state

    @workflow.signal(name=SIGNAL_SUBMIT_DECISION)
    async def submit_decision(self, decision: ApprovalDecision) -> None:
        """Receive a reviewer's verdict.

        The gateway checks state before signalling, but signals are fire-and-forget and
        that check can race. This handler is the authority: a decision that arrives
        before the pause, or a second decision, is dropped rather than applied.
        """

        pending = self._state.pending_action
        if (
            self._state.status is not RunStatus.WAITING_APPROVAL
            or self._decision is not None
            or pending is None
            or decision.review_id != pending.review_id
        ):
            workflow.logger.warning(
                "decision ignored: ticket is not awaiting review",
                extra={"status": self._state.status, "reviewer": decision.reviewer},
            )
            return
        self._decision = decision
        workflow.logger.info(
            "review_decision",
            extra={
                "ticket_id": self._state.ticket_id,
                "review_id": decision.review_id,
                "stage": pending.action,
                "decision": decision.decision,
            },
        )

    @workflow.run
    async def run(self, request: TicketRequest, approval_timeout_hours: int = 0) -> RunState:
        """Run a ticket to completion.

        `approval_timeout_hours` is supplied by the caller rather than read from
        Settings: workflow code cannot touch the filesystem or environment, and an
        explicit input is visible in the Temporal UI when debugging a stuck run.
        0 waits for a reviewer indefinitely.
        """

        self._approval_timeout_hours = approval_timeout_hours
        ticket_id = workflow.info().workflow_id
        # workflow.now() is replay-safe; datetime.now() is not.
        created_at = workflow.now()
        self._state = RunState(ticket_id=ticket_id, status=RunStatus.RUNNING, created_at=created_at)

        # Temporal supplies durability, so the graph only needs an in-memory saver to
        # support interrupt(). Nothing here is written to an application database.
        app = temporal_graph(SUPPORT_GRAPH).compile(checkpointer=InMemorySaver())
        config: dict[str, Any] = {"configurable": {"thread_id": ticket_id}}

        result = await app.ainvoke(
            {
                "ticket_id": ticket_id,
                "customer_id": request.customer_id,
                "message": request.message,
                "order_id": request.order_id,
                "metadata": request.metadata,
                "request_date": created_at.date().isoformat(),
            },
            config,
            version="v2",
        )

        while result.interrupts:
            pending = PendingAction.model_validate(result.interrupts[0].value)
            self._decision = None
            self._state = to_run_state(
                ticket_id=ticket_id,
                values=result.value,
                created_at=created_at,
                pending_action=pending,
            )
            workflow.logger.info(
                "review_waiting",
                extra={
                    "ticket_id": ticket_id,
                    "review_id": pending.review_id,
                    "stage": pending.action,
                    "safety_flags": self._state.safety_flags,
                },
            )
            decision = await self._await_decision(pending.review_id)
            self._decision = None
            self._state = self._state.model_copy(
                update={
                    "status": RunStatus.RUNNING,
                    "pending_action": None,
                }
            )
            result = await app.ainvoke(
                Command(resume=decision.model_dump(mode="json")), config, version="v2"
            )

        self._state = to_run_state(ticket_id=ticket_id, values=result.value, created_at=created_at)
        return self._state

    async def _await_decision(self, review_id: str) -> ApprovalDecision:
        """Block until a reviewer decides, or until the approval deadline passes.

        An unbounded wait leaves workflows open forever when nobody reviews. A deadline
        auto-rejects instead, which leaves the refund unpaid — the safe default.
        """

        timeout = (
            timedelta(hours=self._approval_timeout_hours) if self._approval_timeout_hours else None
        )
        try:
            await workflow.wait_condition(lambda: self._decision is not None, timeout=timeout)
        except TimeoutError:
            workflow.logger.warning("approval timed out; auto-rejecting")
            return ApprovalDecision(
                review_id=review_id,
                decision="reject",
                reviewer="system",
                comment="approval timed out",
            )
        assert self._decision is not None
        return self._decision
