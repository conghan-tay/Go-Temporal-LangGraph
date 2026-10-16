import pytest
from app.core.schemas import PendingAction, RunStatus
from app.temporal.mapping import citations, to_run_state


def test_citations_are_unique_and_ordered() -> None:
    assert citations("See [refund-policy] and [shipping-policy] and [refund-policy].") == [
        "refund-policy",
        "shipping-policy",
    ]


def test_citations_tolerate_a_missing_answer() -> None:
    assert citations(None) == []


def test_completed_state_reports_category_and_citations() -> None:
    state = to_run_state(
        ticket_id="ticket-1",
        values={
            "status": "completed",
            "final_answer": "Your order ships soon. [shipping-policy]",
            "classification": {"category": "order_status"},
        },
    )

    assert state.status is RunStatus.COMPLETED
    assert state.category == "order_status"
    assert state.citations == ["shipping-policy"]
    assert state.answer == "Your order ships soon."
    assert state.pending_action is None


def test_pending_action_forces_waiting_approval() -> None:
    """The interrupt is not part of graph state, so the caller supplies it."""

    state = to_run_state(
        ticket_id="ticket-2",
        values={"draft": "A refund is proposed. [refund-policy]"},
        pending_action=PendingAction(
            review_id="ticket-2:business_action", action="refund", arguments={}, reason="requested"
        ),
    )

    assert state.status is RunStatus.WAITING_APPROVAL
    # While paused there is no final answer yet, so citations come from the draft.
    assert state.citations == ["refund-policy"]
    assert state.answer is None


def test_absent_state_is_treated_as_empty() -> None:
    """A graph result can carry no state, so the mapping normalizes it rather than
    making every caller guard."""

    state = to_run_state(ticket_id="ticket-4", values=None)

    assert state.status is RunStatus.COMPLETED
    assert state.answer is None
    assert state.category is None
    assert state.citations == []


def test_rejected_status_is_preserved() -> None:
    state = to_run_state(
        ticket_id="ticket-3",
        values={"status": "rejected", "final_answer": "The proposed refund was not approved."},
    )

    assert state.status is RunStatus.REJECTED


def test_safety_flags_survive_projection():
    assert to_run_state(
        ticket_id="t", values={"safety_flags": ["prompt_injection:role_impersonation"]}
    ).safety_flags == ["prompt_injection:role_impersonation"]


@pytest.mark.parametrize(
    ("raw", "expected", "sources"),
    [
        ("Eligible. [refund-policy]", "Eligible.", ["refund-policy"]),
        (
            "Eligible [lookup_order] [refund-policy].",
            "Eligible.",
            ["lookup_order", "refund-policy"],
        ),
        ("Eligible[lookup_order][refund-policy]", "Eligible", ["lookup_order", "refund-policy"]),
        ("A [refund-policy] claim [refund-policy].", "A claim.", ["refund-policy"]),
        ("[lookup_order] Eligible.", "Eligible.", ["lookup_order"]),
        ("[lookup_order][refund-policy]", "", ["lookup_order", "refund-policy"]),
        (
            "Eligible [refund-policy], pending review.",
            "Eligible, pending review.",
            ["refund-policy"],
        ),
        (
            "**$25 refund** [lookup_order].\n\nReview required. [refund-policy]",
            "**$25 refund**.\n\nReview required.",
            ["lookup_order", "refund-policy"],
        ),
        (
            "- Eligible [refund-policy]\n  - **$25** [lookup_order]",
            "- Eligible\n  - **$25**",
            ["refund-policy", "lookup_order"],
        ),
        ("    [refund-policy] Indented text", "    Indented text", ["refund-policy"]),
        ("Keep  these spaces. [refund-policy]", "Keep  these spaces.", ["refund-policy"]),
        (
            "**No citations**\n\n  Keep  formatting.\n",
            "**No citations**\n\n  Keep  formatting.\n",
            [],
        ),
        ("", "", []),
        (None, None, []),
    ],
)
def test_answer_is_cleaned_after_extracting_citations(raw, expected, sources):
    values = {"status": "completed", "final_answer": raw}
    state = to_run_state(ticket_id="ticket-clean", values=values)
    assert state.answer == expected
    assert state.citations == sources
    assert values["final_answer"] == raw


def test_draft_citations_and_internal_values_are_preserved():
    values = {"draft": "Review the **$25 refund**. [lookup_order] [refund-policy]"}
    state = to_run_state(
        ticket_id="ticket-review",
        values=values,
        pending_action=PendingAction(
            review_id="ticket-review:business_action",
            action="refund",
            arguments={},
            reason="review",
        ),
    )
    assert state.answer is None
    assert state.citations == ["lookup_order", "refund-policy"]
    assert values["draft"].endswith("[lookup_order] [refund-policy]")


@pytest.mark.parametrize("source_ids", [["refund-policy", "lookup_order", "refund-policy"], []])
def test_explicit_citations_take_precedence_over_answer_and_draft_markers(source_ids):
    state = to_run_state(
        ticket_id="completed",
        values={
            "final_answer": "Your $25.00 refund request has been approved and submitted.",
            "draft": "Pending review. [old-draft-source]",
            "citations": source_ids,
        },
    )
    assert state.citations == list(dict.fromkeys(source_ids))
    assert "[" not in state.answer
