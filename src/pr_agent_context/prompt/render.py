from __future__ import annotations

import hashlib
from pathlib import Path

from pr_agent_context import __version__
from pr_agent_context.constants import (
    COPILOT_COMMENT_SECTION,
    DEFAULT_ALL_CLEAR_PROMPT,
    DEFAULT_CHARACTERS_PER_LINE,
    DEFAULT_FAILURE_EXCERPT_CHARS,
    DEFAULT_FAILURE_EXCERPT_MAX_LINES,
    DEFAULT_ITEM_BUDGET_FLOOR,
    DEFAULT_PATCH_SECTION_HARD_LIMIT,
    DEFAULT_PROMPT_OPENING,
    DEFAULT_REPLY_BODY_CHARS,
    DEFAULT_ROOT_COMMENT_BODY_CHARS,
    DEFAULT_SECTION_BUDGETS,
    DEFAULT_TOOL_REF,
    FAILING_WORKFLOWS_SECTION,
    PATCH_COVERAGE_SECTION,
    REVIEW_COMMENT_SECTION,
)
from pr_agent_context.domain.models import (
    FailingCheck,
    ManagedCommentIdentity,
    PatchCoverageSummary,
    RenderedPrompt,
    ReviewMessage,
    ReviewThread,
    TruncationNote,
)
from pr_agent_context.github.comment_markers import format_managed_comment_marker
from pr_agent_context.prompt.line_wrap import wrap_markdown_prose
from pr_agent_context.prompt.template import load_prompt_template, render_prompt_template
from pr_agent_context.prompt.truncate import truncate_lines, truncate_text


def render_prompt(
    *,
    pull_request_number: int,
    head_sha: str | None = None,
    run_id: int = 0,
    run_attempt: int = 1,
    tool_ref: str = DEFAULT_TOOL_REF,
    tool_version: str = __version__,
    review_threads: list[ReviewThread],
    failing_checks: list[FailingCheck],
    patch_coverage: PatchCoverageSummary | None = None,
    include_review_comments: bool = True,
    include_failing_checks: bool = True,
    include_patch_coverage: bool = True,
    prompt_preamble: str = "",
    force_patch_coverage_section: bool = False,
    prompt_template_file: Path | None = None,
    characters_per_line: int = DEFAULT_CHARACTERS_PER_LINE,
) -> RenderedPrompt:
    truncation_notes: list[TruncationNote] = []
    has_review_items = include_review_comments and bool(review_threads)
    has_failing_check_items = include_failing_checks and bool(failing_checks)
    has_patch_coverage_items = include_patch_coverage and bool(
        patch_coverage and patch_coverage.actionable
    )
    has_actionable_items = bool(
        has_review_items or has_failing_check_items or has_patch_coverage_items
    )

    copilot_threads = [thread for thread in review_threads if thread.classifier == "copilot"]
    review_only_threads = [thread for thread in review_threads if thread.classifier != "copilot"]

    copilot_section, notes = _render_review_threads_section(
        COPILOT_COMMENT_SECTION,
        copilot_threads,
        section_key="copilot_comments_section",
    )
    truncation_notes.extend(notes)
    review_section, notes = _render_review_threads_section(
        REVIEW_COMMENT_SECTION,
        review_only_threads,
        section_key="review_comments_section",
    )
    truncation_notes.extend(notes)
    failing_section, notes = _render_failing_checks_section(failing_checks)
    truncation_notes.extend(notes)
    patch_section, notes = _render_patch_coverage_section(
        patch_coverage,
        force_patch_coverage_section=force_patch_coverage_section,
    )
    truncation_notes.extend(notes)

    template_text, template_path, template_source = load_prompt_template(prompt_template_file)
    prompt_markdown, diagnostics = render_prompt_template(
        template_text=template_text,
        template_source=template_source,
        template_path=template_path,
        values={
            "pr_number": str(pull_request_number),
            "prompt_preamble": prompt_preamble.strip(),
            "opening_instructions": _build_opening_instructions(
                pull_request_number=pull_request_number,
                head_sha=head_sha,
                run_id=run_id,
                run_attempt=run_attempt,
                tool_ref=tool_ref,
                tool_version=tool_version,
                has_actionable_items=has_actionable_items,
                include_review_comments=include_review_comments,
                include_failing_checks=include_failing_checks,
                include_patch_coverage=include_patch_coverage,
            ),
            "copilot_comments_section": copilot_section,
            "review_comments_section": review_section,
            "failing_checks_section": failing_section,
            "patch_coverage_section": patch_section,
        },
    )
    prompt_markdown = wrap_markdown_prose(
        prompt_markdown,
        max_chars=characters_per_line,
    )
    prompt_sha256 = hashlib.sha256(prompt_markdown.encode("utf-8")).hexdigest()
    comment_body = build_managed_comment_body(
        prompt_markdown,
        pull_request_number=pull_request_number,
        run_id=run_id,
        run_attempt=run_attempt,
        head_sha=head_sha or "unknown",
        tool_ref=tool_ref,
    )
    return RenderedPrompt(
        prompt_markdown=prompt_markdown,
        comment_body=comment_body,
        prompt_sha256=prompt_sha256,
        has_actionable_items=has_actionable_items,
        should_publish_comment=True,
        truncation_notes=truncation_notes,
        template_diagnostics=diagnostics,
    )


def build_managed_comment_body(
    markdown: str,
    *,
    pull_request_number: int,
    run_id: int,
    run_attempt: int,
    head_sha: str,
    tool_ref: str,
) -> str:
    marker = format_managed_comment_marker(
        ManagedCommentIdentity(
            pull_request_number=pull_request_number,
            run_id=run_id,
            run_attempt=run_attempt,
            head_sha=head_sha,
            tool_ref=tool_ref,
        )
    )
    return f"{marker}\n{_wrap_markdown_code_block(markdown)}"


def _build_opening_instructions(
    *,
    pull_request_number: int,
    head_sha: str | None,
    run_id: int,
    run_attempt: int,
    tool_ref: str,
    tool_version: str,
    has_actionable_items: bool,
    include_review_comments: bool,
    include_failing_checks: bool,
    include_patch_coverage: bool,
) -> str:
    if has_actionable_items:
        return DEFAULT_PROMPT_OPENING.format(
            pr_number=pull_request_number,
            head_sha=head_sha or "unknown",
            run_id=run_id,
            run_attempt=run_attempt,
            tool_ref=tool_ref,
            tool_version=tool_version,
        )

    disabled_checks = [
        label
        for label, enabled in (
            ("review comments", include_review_comments),
            ("failing checks", include_failing_checks),
            ("patch coverage", include_patch_coverage),
        )
        if not enabled
    ]
    if not disabled_checks:
        return DEFAULT_ALL_CLEAR_PROMPT.format(
            pr_number=pull_request_number,
            head_sha=head_sha or "unknown",
            run_id=run_id,
            run_attempt=run_attempt,
            tool_ref=tool_ref,
            tool_version=tool_version,
        )
    return (
        "No actionable items were found in the enabled checks for PR "
        f"#{pull_request_number} at head commit {head_sha or 'unknown'}."
        + "\n\n"
        + "Note: This assessment only covers the enabled checks for this run. "
        + "Skipped checks: "
        + ", ".join(disabled_checks)
        + "."
    )


def _render_review_threads_section(
    title: str,
    threads: list[ReviewThread],
    *,
    section_key: str,
) -> tuple[str, list[TruncationNote]]:
    if not threads:
        return "", []

    budget = DEFAULT_SECTION_BUDGETS[section_key]
    item_blocks: list[str] = []
    notes: list[TruncationNote] = []
    for index, thread in enumerate(threads, start=1):
        remaining_items = len(threads) - index + 1
        used_budget = sum(len(block) for block in item_blocks)
        remaining_budget = max(0, budget - used_budget)
        if remaining_budget <= 0:
            break
        item_budget = max(DEFAULT_ITEM_BUDGET_FLOOR, remaining_budget // remaining_items)
        rendered, item_notes = _render_review_thread(thread, max_chars=item_budget)
        item_blocks.append(rendered)
        notes.extend(item_notes)
    section_text = f"# {title}\n\n" + "\n\n".join(item_blocks)
    if len(section_text) <= budget:
        return section_text, notes
    trimmed, note = truncate_text(
        section_text,
        max_chars=budget,
        target=section_key,
        strategy="section_budget_cap",
        suffix="\n[note: section truncated to fit overall section budget]",
    )
    if note:
        notes.append(note)
    return trimmed, notes


def _render_review_thread(
    thread: ReviewThread,
    *,
    max_chars: int,
) -> tuple[str, list[TruncationNote]]:
    notes: list[TruncationNote] = []
    location = _format_location(thread.path, thread.line)
    root = thread.messages[0]
    lines = [
        f"## {thread.item_id}",
        f"Location: {location}",
        f"URL: {thread.url}",
        f"Root author: {root.author_login}",
        "",
        "Comment:",
    ]
    root_body, note = truncate_text(
        _sanitize_block(root.body),
        max_chars=DEFAULT_ROOT_COMMENT_BODY_CHARS,
        target=thread.item_id or "review-thread",
        strategy="truncate_root_comment",
        suffix="\n[comment truncated]",
    )
    if note:
        notes.append(note)
    lines.append(_indent_block(root_body))

    replies = thread.messages[1:]
    if replies:
        lines.extend(["", "Replies:"])
        for reply in replies:
            rendered_reply, reply_notes = _render_reply(
                reply,
                target=thread.item_id or "review-thread",
            )
            lines.extend(rendered_reply)
            notes.extend(reply_notes)

    block = "\n".join(lines)
    if len(block) <= max_chars:
        return block, notes

    root_author_line = f"Root author: {root.author_login}"
    lines = [line for line in lines if line != root_author_line]
    metadata_note = TruncationNote(
        target=thread.item_id or "review-thread",
        strategy="drop_metadata",
        message="Dropped less-important metadata to fit section budget.",
        original_size=len(block),
        truncated_size=len("\n".join(lines)),
    )
    notes.append(metadata_note)
    block = "\n".join(lines)

    if len(block) <= max_chars:
        return block, notes

    trimmed, note = truncate_text(
        block,
        max_chars=max_chars,
        target=thread.item_id or "review-thread",
        strategy="section_budget",
        suffix="\n[note: thread truncated to fit section budget]",
    )
    if note:
        notes.append(note)
    return trimmed, notes


def _render_reply(
    reply: ReviewMessage,
    *,
    target: str,
) -> tuple[list[str], list[TruncationNote]]:
    notes: list[TruncationNote] = []
    body, note = truncate_text(
        _sanitize_block(reply.body),
        max_chars=DEFAULT_REPLY_BODY_CHARS,
        target=target,
        strategy="truncate_reply_body",
        suffix="\n[reply truncated]",
    )
    if note:
        notes.append(note)
    return [f"- {reply.author_login}", _indent_block(body, indent="  ")], notes


def _render_failing_checks_section(
    failures: list[FailingCheck],
) -> tuple[str, list[TruncationNote]]:
    if not failures:
        return "", []

    budget = DEFAULT_SECTION_BUDGETS["failing_checks_section"]
    item_blocks: list[str] = []
    notes: list[TruncationNote] = []
    for index, failure in enumerate(failures, start=1):
        remaining_items = len(failures) - index + 1
        used_budget = sum(len(block) for block in item_blocks)
        remaining_budget = max(0, budget - used_budget)
        if remaining_budget <= 0:
            break
        item_budget = max(DEFAULT_ITEM_BUDGET_FLOOR, remaining_budget // remaining_items)
        rendered, item_notes = _render_failing_check(failure, max_chars=item_budget)
        item_blocks.append(rendered)
        notes.extend(item_notes)
    section_text = f"# {FAILING_WORKFLOWS_SECTION}\n\n" + "\n\n".join(item_blocks)
    if len(section_text) <= budget:
        return section_text, notes
    trimmed, note = truncate_text(
        section_text,
        max_chars=budget,
        target="failing_checks_section",
        strategy="section_budget_cap",
        suffix="\n[note: section truncated to fit overall section budget]",
    )
    if note:
        notes.append(note)
    return trimmed, notes


def _render_failing_check(
    failure: FailingCheck,
    *,
    max_chars: int,
) -> tuple[str, list[TruncationNote]]:
    notes: list[TruncationNote] = []
    lines = [
        f"## {failure.item_id}",
        f"Type: {_format_failure_type(failure)}",
    ]
    if failure.source_type in {"actions_job", "actions_workflow_run"}:
        lines.append(f"Workflow: {failure.workflow_name}")
    elif failure.source_type == "external_check_run":
        lines.append(f"App: {failure.app_name or failure.workflow_name}")
        lines.append(f"Check: {failure.job_name}")
    else:
        lines.append(f"Context: {failure.context_name or failure.job_name}")

    if failure.source_type == "actions_job":
        lines.append(f"Job: {failure.job_name}")
    elif failure.source_type == "actions_workflow_run":
        lines.append(f"Run: {failure.job_name}")

    if failure.matrix_label:
        lines.append(f"Matrix: {failure.matrix_label}")
    if failure.run_number:
        lines.append(f"Run number: {failure.run_number}")
    if failure.run_attempt and failure.source_type in {"actions_job", "actions_workflow_run"}:
        lines.append(f"Run attempt: {failure.run_attempt}")
    if failure.status and failure.source_type in {"external_check_run", "commit_status"}:
        lines.append(f"Status: {failure.status}")
    if failure.conclusion:
        lines.append(f"Conclusion: {failure.conclusion}")
    if failure.is_current_run:
        lines.append("Current run: yes")
    lines.append(f"URL: {failure.url or '(not available)'}")
    if failure.failed_steps:
        lines.append(f"Failed steps: {', '.join(failure.failed_steps)}")
    if failure.summary:
        lines.extend(["", "Summary:", _indent_block(_sanitize_block(failure.summary))])
    if failure.excerpt_lines:
        excerpt_lines, note = truncate_lines(
            failure.excerpt_lines,
            max_lines=min(len(failure.excerpt_lines), DEFAULT_FAILURE_EXCERPT_MAX_LINES),
            max_chars=DEFAULT_FAILURE_EXCERPT_CHARS,
            target=failure.item_id or "workflow-failure",
            strategy="trim_log_excerpt",
            note_message="Excerpt truncated to fit section budget.",
        )
        if note:
            notes.append(note)
            excerpt_lines = excerpt_lines + [
                (
                    "[note: excerpt truncated to "
                    f"{len(excerpt_lines)} of {len(failure.excerpt_lines)} lines]"
                ),
            ]
        lines.extend(
            [
                "",
                "Excerpt:",
                _indent_block(_sanitize_block("\n".join(excerpt_lines))),
            ]
        )

    block = "\n".join(lines)
    if len(block) <= max_chars:
        return block, notes

    lines = [
        line
        for line in lines
        if not line.startswith(("Matrix: ", "Run attempt: ", "Current run: "))
    ]
    metadata_note = TruncationNote(
        target=failure.item_id or "workflow-failure",
        strategy="drop_metadata",
        message="Dropped less-important metadata to fit section budget.",
        original_size=len(block),
        truncated_size=len("\n".join(lines)),
    )
    notes.append(metadata_note)
    block = "\n".join(lines)

    if len(block) <= max_chars:
        return block, notes

    trimmed, note = truncate_text(
        block,
        max_chars=max_chars,
        target=failure.item_id or "workflow-failure",
        strategy="section_budget",
        suffix="\n[note: failure details truncated to fit section budget]",
    )
    if note:
        notes.append(note)
    return trimmed, notes


def _format_failure_type(failure: FailingCheck) -> str:
    labels = {
        "actions_job": "GitHub Actions job",
        "actions_workflow_run": "GitHub Actions workflow run",
        "external_check_run": "External check run",
        "commit_status": "Commit status",
    }
    return labels[failure.source_type]


def _render_patch_coverage_section(
    patch_coverage: PatchCoverageSummary | None,
    *,
    force_patch_coverage_section: bool,
) -> tuple[str, list[TruncationNote]]:
    if patch_coverage is None:
        return "", []
    if not patch_coverage.actionable and not force_patch_coverage_section:
        return "", []
    if patch_coverage.is_na:
        return (
            f"# {PATCH_COVERAGE_SECTION}\n\n"
            "There are no changed executable Python lines in the patch."
        ), []
    if patch_coverage.actual_percent is None:
        return (
            f"# {PATCH_COVERAGE_SECTION}\n\n"
            "Patch coverage could not be determined from the available coverage artifacts.",
            [],
        )

    if patch_coverage.actionable:
        lines = [
            f"# {PATCH_COVERAGE_SECTION}",
            "",
            (
                "Codecov shows patch test coverage is "
                f"{_format_percent(patch_coverage.actual_percent)}; "
                f"please raise it to {_format_percent(patch_coverage.target_percent)}. "
                "These are the uncovered code lines:"
            ),
        ]
        for file_gap in patch_coverage.files:
            if not file_gap.uncovered_changed_executable_lines:
                continue
            uncovered_lines = ", ".join(
                str(line) for line in file_gap.uncovered_changed_executable_lines
            )
            lines.append(f"- {file_gap.path}: {uncovered_lines}")
        block = "\n".join(lines)
        if len(block) <= DEFAULT_PATCH_SECTION_HARD_LIMIT:
            return block, []
        trimmed, note = truncate_text(
            block,
            max_chars=DEFAULT_PATCH_SECTION_HARD_LIMIT,
            target="patch-coverage",
            strategy="hard_limit",
            suffix=(
                "\n[note: patch coverage section truncated only after preserving "
                "the explicit uncovered line list prefix]"
            ),
        )
        return trimmed, [note] if note else []

    return (
        f"# {PATCH_COVERAGE_SECTION}\n\n"
        "Patch coverage is "
        f"{_format_percent(patch_coverage.actual_percent)}, meeting the target of "
        f"{_format_percent(patch_coverage.target_percent)}.",
        [],
    )


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


def _wrap_markdown_code_block(text: str) -> str:
    fence = "```"
    if fence in text:
        alternative = "~~~"
        if alternative not in text:
            fence = alternative
        else:
            while fence in text:
                fence += "`"
    return f"{fence}markdown\n{text}\n{fence}"


def _format_percent(value: float) -> str:
    rounded = round(value, 2)
    if rounded.is_integer():
        return f"{int(rounded)}%"
    return f"{rounded:.2f}%"
