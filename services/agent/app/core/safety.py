import re
from dataclasses import dataclass


@dataclass(frozen=True)
class SafetyResult:
    normalized_text: str
    flags: tuple[str, ...]


# These checks are intentionally transparent and replaceable. In a regulated system,
# add a provider moderation API and organization-specific policy engine at this boundary.
_PROMPT_INJECTION_PATTERNS = (
    (
        "prompt_injection:instruction_override",
        re.compile(r"ignore (all|any|the) previous instructions", re.IGNORECASE),
    ),
    (
        "prompt_injection:prompt_disclosure",
        re.compile(r"reveal (the )?(system|developer) prompt", re.IGNORECASE),
    ),
    (
        "prompt_injection:role_impersonation",
        re.compile(r"act as (an? )?(admin|system)", re.IGNORECASE),
    ),
)
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def inspect_user_text(text: str, max_chars: int) -> SafetyResult:
    """Normalize untrusted input and flag common prompt-injection attempts.

    Flags identify matched patterns, not an exhaustive injection classifier.
    The graph must obtain input-review approval before passing flagged text downstream.
    Normalization does not remove suspicious instructions.
    """

    cleaned = _CONTROL_CHARS.sub("", text).strip()[:max_chars]
    flags = tuple(name for name, pattern in _PROMPT_INJECTION_PATTERNS if pattern.search(cleaned))
    return SafetyResult(normalized_text=cleaned, flags=tuple(dict.fromkeys(flags)))


def safe_context(documents: list[dict[str, str]]) -> str:
    """Delimit retrieved text so knowledge-base content remains data, not instructions."""

    chunks = []
    for document in documents:
        chunks.append(
            '<knowledge source="{}">\n{}\n</knowledge>'.format(
                document.get("source", "unknown"), document.get("content", "")
            )
        )
    return "\n\n".join(chunks)
