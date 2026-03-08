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
    if max_chars <= 0:
        note = TruncationNote(
            target=target,
            strategy=strategy,
            message=suffix,
            original_size=len(text),
            truncated_size=0,
        )
        return "", note
    if len(text) <= max_chars:
        return text, None
    if max_chars <= len(suffix):
        truncated_suffix = suffix[:max_chars]
        note = TruncationNote(
            target=target,
            strategy=strategy,
            message=suffix,
            original_size=len(text),
            truncated_size=len(truncated_suffix),
        )
        return truncated_suffix, note
    clipped = text[: max(0, max_chars - len(suffix))].rstrip()
    truncated_text = f"{clipped}{suffix}"
    note = TruncationNote(
        target=target,
        strategy=strategy,
        message=suffix,
        original_size=len(text),
        truncated_size=len(truncated_text),
    )
    return truncated_text, note


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
    truncated_joined = "\n".join(truncated)
    note = TruncationNote(
        target=target,
        strategy=strategy,
        message=note_message,
        original_size=len(joined),
        truncated_size=len(truncated_joined),
    )
    return truncated, note
