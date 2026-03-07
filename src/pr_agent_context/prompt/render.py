from __future__ import annotations

from pr_agent_context.constants import (
    COPILOT_COMMENT_SECTION,
    DEFAULT_PROMPT_OPENING,
    FAILING_JOBS_SECTION,
    MANAGED_COMMENT_MARKER,
    REVIEW_COMMENT_SECTION,
)
from pr_agent_context.domain.models import RenderedPrompt, ReviewThread, WorkflowFailure


def render_prompt(
    *,
    pull_request_number: int,
    review_threads: list[ReviewThread],
    workflow_failures: list[WorkflowFailure],
    prompt_preamble: str = "",
) -> RenderedPrompt:
    sections: list[str] = []
    if prompt_preamble:
        sections.append(prompt_preamble.strip())

    sections.append(DEFAULT_PROMPT_OPENING.format(pr_number=pull_request_number))

    copilot_threads = [thread for thread in review_threads if thread.classifier == "copilot"]
    review_only_threads = [thread for thread in review_threads if thread.classifier != "copilot"]

    if copilot_threads:
        sections.append(
            f"# {COPILOT_COMMENT_SECTION}\n\n"
            + "\n\n".join(_render_review_thread(thread) for thread in copilot_threads)
        )
    if review_only_threads:
        sections.append(
            f"# {REVIEW_COMMENT_SECTION}\n\n"
            + "\n\n".join(_render_review_thread(thread) for thread in review_only_threads)
        )
    if workflow_failures:
        sections.append(
            f"# {FAILING_JOBS_SECTION}\n\n"
            + "\n\n".join(_render_workflow_failure(failure) for failure in workflow_failures)
        )

    prompt_markdown = "\n\n".join(section.strip() for section in sections if section.strip())
    comment_body = f"{MANAGED_COMMENT_MARKER}\n```markdown\n{prompt_markdown}\n```"
    return RenderedPrompt(
        prompt_markdown=prompt_markdown,
        comment_body=comment_body,
        has_actionable_items=bool(review_threads or workflow_failures),
    )


def _render_review_thread(thread: ReviewThread) -> str:
    location = _format_location(thread.path, thread.line)
    root = thread.messages[0]
    lines = [
        f"## {thread.item_id}",
        f"Location: {location}",
        f"URL: {thread.url}",
        f"Root author: {root.author_login}",
        "",
        "Comment:",
        _indent_block(_sanitize_block(root.body)),
    ]
    replies = thread.messages[1:]
    if replies:
        lines.extend(["", "Replies:"])
        for reply in replies:
            lines.append(f"- {reply.author_login}")
            lines.append(_indent_block(_sanitize_block(reply.body), indent="  "))
    return "\n".join(lines)


def _render_workflow_failure(failure: WorkflowFailure) -> str:
    lines = [
        f"## {failure.item_id}",
        f"Workflow: {failure.workflow_name}",
        f"Job: {failure.job_name}",
    ]
    if failure.matrix_label:
        lines.append(f"Matrix: {failure.matrix_label}")
    lines.append(f"URL: {failure.url}")
    if failure.failed_steps:
        lines.append(f"Failed steps: {', '.join(failure.failed_steps)}")
    if failure.excerpt_lines:
        lines.extend(
            [
                "",
                "Excerpt:",
                _indent_block(_sanitize_block("\n".join(failure.excerpt_lines))),
            ]
        )
    return "\n".join(lines)


def _format_location(path: str | None, line: int | None) -> str:
    if not path:
        return "unknown"
    if line is None:
        return path
    return f"{path}:{line}"


def _indent_block(text: str, *, indent: str = "    ") -> str:
    return "\n".join(f"{indent}{line}" if line else indent.rstrip() for line in text.splitlines())


def _sanitize_block(text: str) -> str:
    normalized = text.replace("\r\n", "\n").strip()
    return normalized.replace("```", "~~~")
