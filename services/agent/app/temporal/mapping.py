"""Pure translation between LangGraph state and the API-facing RunState.

Kept free of Temporal and LangGraph imports so it can be unit tested directly. This
logic used to live in the FastAPI response helpers.
"""

import re
from datetime import datetime
from typing import Any

from ..core.schemas import PendingAction, RunState, RunStatus

_CITATION_PATTERN = re.compile(r"\[([^\]]+)\]")
_INLINE_CITATIONS = re.compile(rf"[ \t]*(?:{_CITATION_PATTERN.pattern}[ \t]*)+")


def citations(answer: str | None) -> list[str]:
    """Extract unique square-bracket citations, preserving first-seen order."""

    return list(dict.fromkeys(_CITATION_PATTERN.findall(answer or "")))


def without_inline_citations(answer: str | None) -> str | None:
    """Remove citation markers and their spacing without reformatting other text."""

    if answer is None:
        return None

    def replacement(match: re.Match[str]) -> str:
        before = answer[match.start() - 1 : match.start()] if match.start() else ""
        after = answer[match.end() : match.end() + 1]
        if not after or after in "\r\n" or after in ".,;:!?":
            return ""
        if not before or before in "\r\n":
            # Keep Markdown indentation, but discard spacing after a leading marker.
            return re.match(r"[ \t]*", match.group()).group()
        return " " if _CITATION_PATTERN.sub("", match.group()) else ""

    cleaned = _INLINE_CITATIONS.sub(replacement, answer)
    return cleaned.strip("\r\n") if cleaned != answer else answer


def to_run_state(
    *,
    ticket_id: str,
    values: dict[str, Any] | None,
    created_at: datetime | None = None,
    pending_action: PendingAction | None = None,
) -> RunState:
    """Project final graph state onto the public run state.

    `values` accepts None so callers can pass a graph result straight through: an
    interrupted run can carry no state yet, and that is a mapping concern rather than
    something every caller should normalize.

    `pending_action` is supplied by the caller rather than read from state because the
    pause is expressed as a LangGraph interrupt, which never lands in the state dict.
    """

    values = values or {}
    answer = values.get("final_answer")
    # Extract before cleaning; internal drafts remain intact for critique/grounding.
    source_ids = (
        list(dict.fromkeys(values["citations"]))
        if "citations" in values
        else citations(answer or values.get("draft"))
    )
    classification = values.get("classification")
    status = (
        RunStatus.WAITING_APPROVAL
        if pending_action
        else RunStatus(values.get("status", RunStatus.COMPLETED))
    )
    return RunState(
        ticket_id=ticket_id,
        status=status,
        answer=without_inline_citations(answer),
        category=classification.get("category") if classification else None,
        pending_action=pending_action,
        citations=source_ids,
        created_at=created_at,
        safety_flags=values.get("safety_flags", []),
    )
