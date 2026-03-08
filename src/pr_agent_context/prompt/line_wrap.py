from __future__ import annotations

import re
import textwrap

LIST_ITEM_RE = re.compile(r"^(\s*)([-+*]|\d+\.)\s+")
METADATA_PREFIXES = (
    "Location:",
    "URL:",
    "Root author:",
    "Comment:",
    "Replies:",
    "Failed steps:",
    "Excerpt:",
)


def wrap_markdown_prose(text: str, *, max_chars: int) -> str:
    if max_chars <= 0:
        return text

    wrapped_lines: list[str] = []
    in_fenced_block = False

    for line in text.splitlines():
        if _is_markdown_fence(line):
            in_fenced_block = not in_fenced_block
            wrapped_lines.append(line)
            continue

        if in_fenced_block or not _is_wrappable_prose_line(line, max_chars=max_chars):
            wrapped_lines.append(line)
            continue

        wrapped_lines.extend(
            textwrap.wrap(
                line,
                width=max_chars,
                break_long_words=False,
                break_on_hyphens=False,
            )
        )

    return "\n".join(wrapped_lines)


def _is_markdown_fence(line: str) -> bool:
    leading_spaces = len(line) - len(line.lstrip(" "))
    if leading_spaces > 3:
        return False
    stripped = line.strip()
    return stripped.startswith("```") or stripped.startswith("~~~")


def _is_wrappable_prose_line(line: str, *, max_chars: int) -> bool:
    if len(line) <= max_chars:
        return False

    stripped = line.strip()
    if not stripped:
        return False
    if line.startswith("    ") or line.startswith("\t"):
        return False
    if stripped.startswith("#"):
        return False
    if stripped.startswith(">"):
        return False
    if LIST_ITEM_RE.match(line):
        return False
    if any(stripped.startswith(prefix) for prefix in METADATA_PREFIXES):
        return False
    if "http://" in line or "https://" in line:
        return False

    return True
