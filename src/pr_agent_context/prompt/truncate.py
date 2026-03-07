from __future__ import annotations

from pr_agent_context.domain.models import TruncationNote


def truncate_text(
    text: str,
    *,
    max_chars: int,
    target: str,
    strategy: str,
    suffix: str,
) -> tuple[str, TruncationNote | None]:
    if max_chars <= 0 or len(text) <= max_chars:
        return text, None
    clipped = text[: max(0, max_chars - len(suffix))].rstrip()
    note = TruncationNote(
        target=target,
        strategy=strategy,
        message=suffix,
        original_size=len(text),
        truncated_size=len(clipped),
    )
    return f"{clipped}{suffix}", note


def truncate_lines(
    lines: list[str],
    *,
    max_lines: int,
    max_chars: int,
    target: str,
    strategy: str,
    note_message: str,
) -> tuple[list[str], TruncationNote | None]:
    joined = "\n".join(lines)
    if len(lines) <= max_lines and len(joined) <= max_chars:
        return lines, None

    truncated = lines[:max_lines]
    while truncated and len("\n".join(truncated)) > max_chars:
        truncated = truncated[:-1]
    note = TruncationNote(
        target=target,
        strategy=strategy,
        message=note_message,
        original_size=len(lines),
        truncated_size=len(truncated),
    )
    return truncated, note
