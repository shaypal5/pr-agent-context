from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import load_json_fixture, load_text_fixture
from pr_agent_context.domain.models import (
    FailingCheck,
    PatchCoverageSummary,
    ReviewThread,
    TruncationNote,
)
from pr_agent_context.prompt import render as render_module
from pr_agent_context.prompt.line_wrap import wrap_markdown_prose
from pr_agent_context.prompt.render import (
    _format_location,
    _indent_block,
    _render_failing_check,
    _render_failing_checks_section,
    _render_patch_coverage_section,
    _render_review_thread,
    _render_review_threads_section,
    _sanitize_block,
    _wrap_markdown_code_block,
    build_managed_comment_body,
    render_prompt,
)
from pr_agent_context.prompt.template import (
    load_prompt_template,
    render_prompt_template,
)
from pr_agent_context.prompt.truncate import truncate_lines, truncate_text


def test_render_prompt_matches_expected_snapshots():
    payload = load_json_fixture("prompts/collected_context.json")
    review_threads = [ReviewThread.model_validate(item) for item in payload["review_threads"]]
    failing_checks = [FailingCheck.model_validate(item) for item in payload["failing_checks"]]

    rendered = render_prompt(
        pull_request_number=payload["pull_request_number"],
        head_sha="def456",
        review_threads=review_threads,
        failing_checks=failing_checks,
        prompt_preamble=payload["prompt_preamble"],
    )

    assert rendered.prompt_markdown == load_text_fixture("prompts/expected_prompt.md").strip()
    assert rendered.comment_body == load_text_fixture("prompts/expected_comment.md").strip()
    assert len(rendered.prompt_sha256) == 64
    assert rendered.template_diagnostics.template_source == "built_in"


def test_wrap_markdown_prose_wraps_plain_text_only():
    text = (
        "This is a deliberately long prose sentence that should be wrapped by the renderer "
        "because it is plain narrative text and exceeds the configured width.\n"
        "URL: https://example.com/this/should/not/wrap/even/if/it/is/very/long\n"
        "- src/example.py: 1, 2, 3, 4, 5, 6, 7, 8\n"
        "    indented code line that should stay exactly as-is even when it is very very "
        "very long\n"
        "```python\n"
        "very_long_code_line = 'this should not wrap even when it is significantly longer "
        "than the limit'\n"
        "```"
    )

    wrapped = wrap_markdown_prose(text, max_chars=60)

    assert (
        "This is a deliberately long prose sentence that should be\n"
        "wrapped by the renderer because it is plain narrative text\n"
        "and exceeds the configured width."
    ) in wrapped
    assert "URL: https://example.com/this/should/not/wrap/even/if/it/is/very/long" in wrapped
    assert "- src/example.py: 1, 2, 3, 4, 5, 6, 7, 8" in wrapped
    assert (
        "    indented code line that should stay exactly as-is even when it is very very very long"
        in wrapped
    )
    assert (
        "very_long_code_line = 'this should not wrap even when it is significantly longer "
        "than the limit'" in wrapped
    )


def test_render_prompt_respects_custom_characters_per_line_limit():
    rendered = render_prompt(
        pull_request_number=11,
        head_sha="abc123",
        review_threads=[],
        failing_checks=[],
        prompt_preamble=(
            "This is a deliberately long prose preamble sentence that should be wrapped tightly "
            "for readability in the rendered prompt output."
        ),
        characters_per_line=50,
    )

    first_line = rendered.prompt_markdown.splitlines()[0]
    assert len(first_line) <= 50


def test_wrap_markdown_prose_does_not_treat_indented_fence_as_real_fence():
    text = (
        "    ~~~\n"
        "This is a deliberately long prose sentence that should still be wrapped because the "
        "preceding indented fence-like line is only a snippet.\n"
        "Another long prose sentence that should also wrap because fenced-block state must not "
        "leak from indented content."
    )

    wrapped = wrap_markdown_prose(text, max_chars=60)

    assert "    ~~~" in wrapped
    assert (
        "This is a deliberately long prose sentence that should still\n"
        "be wrapped because the preceding indented fence-like line is\n"
        "only a snippet."
    ) in wrapped
    assert (
        "Another long prose sentence that should also wrap because\n"
        "fenced-block state must not leak from indented content."
    ) in wrapped


def test_wrap_markdown_prose_treats_up_to_three_leading_spaces_as_valid_fence():
    text = (
        "   ```python\n"
        "very_long_code_line = 'this should remain untouched inside a valid fenced block even "
        "when it exceeds the width'\n"
        "   ```\n"
        "This is a deliberately long prose sentence after the fence and it should wrap "
        "normally once the fenced block closes."
    )

    wrapped = wrap_markdown_prose(text, max_chars=60)

    assert "   ```python" in wrapped
    assert (
        "very_long_code_line = 'this should remain untouched inside a valid fenced block even "
        "when it exceeds the width'"
    ) in wrapped
    assert (
        "This is a deliberately long prose sentence after the fence\n"
        "and it should wrap normally once the fenced block closes."
    ) in wrapped


def test_wrap_markdown_prose_does_not_treat_tab_indented_fence_as_real_fence():
    text = (
        "\t~~~\n"
        "This is a deliberately long prose sentence that should still be wrapped because the "
        "preceding tab-indented fence-like line is only a snippet.\n"
        "Another long prose sentence that should also wrap because fenced-block state must not "
        "leak from tab-indented content."
    )

    wrapped = wrap_markdown_prose(text, max_chars=60)

    assert "\t~~~" in wrapped
    assert (
        "This is a deliberately long prose sentence that should still\n"
        "be wrapped because the preceding tab-indented fence-like\n"
        "line is only a snippet."
    ) in wrapped
    assert (
        "Another long prose sentence that should also wrap because\n"
        "fenced-block state must not leak from tab-indented content."
    ) in wrapped


def test_wrap_markdown_prose_returns_original_text_for_non_positive_limit():
    text = "This is a long line that would otherwise wrap if the limit were positive."

    assert wrap_markdown_prose(text, max_chars=0) == text


def test_wrap_markdown_prose_skips_sensitive_long_lines():
    text = "\n".join(
        [
            "      ",
            "# This is a heading line that should stay intact even though it is quite long indeed",
            "> This is a blockquote line that should remain unchanged even though it is quite "
            "long indeed",
            "1. This is a list item that should remain unchanged even though it is quite long "
            "indeed",
            "See https://example.com/very/long/path/that/should/remain/on/one/line/even/when/it/exceeds/the/limit",
        ]
    )

    wrapped = wrap_markdown_prose(text, max_chars=20)

    assert wrapped == text


def test_wrap_markdown_prose_skips_blank_whitespace_only_lines():
    assert wrap_markdown_prose(" " * 40, max_chars=10) == " " * 40


def test_render_prompt_renders_actionable_patch_coverage_section():
    rendered = render_prompt(
        pull_request_number=17,
        head_sha="deadbeef",
        review_threads=[],
        failing_checks=[],
        patch_coverage=PatchCoverageSummary(
            target_percent=100,
            actual_percent=50,
            total_changed_executable_lines=4,
            covered_changed_executable_lines=2,
            files=[
                {
                    "path": "src/pkg/example.py",
                    "changed_added_lines": [1, 2, 3, 4],
                    "changed_executable_lines": [1, 2, 3, 4],
                    "covered_changed_executable_lines": [1, 2],
                    "uncovered_changed_executable_lines": [3, 4],
                    "has_measured_data": True,
                }
            ],
            actionable=True,
            is_na=False,
        ),
    )

    assert "# Patch coverage" in rendered.prompt_markdown
    assert "PR head commit: deadbeef" not in rendered.prompt_markdown
    assert "Patch test coverage is 50%" in rendered.prompt_markdown
    assert "- src/pkg/example.py: 3, 4" in rendered.prompt_markdown


def test_render_helpers_append_truncation_notes(monkeypatch):
    note = TruncationNote(
        target="target",
        strategy="section_budget",
        message="truncated",
        original_size=100,
        truncated_size=20,
    )
    monkeypatch.setattr(render_module, "truncate_text", lambda *args, **kwargs: ("trimmed", note))

    thread = ReviewThread.model_validate(
        {
            "thread_id": "PRRT_note",
            "classifier": "review",
            "item_id": "REVIEW-1",
            "sort_key": 1,
            "path": "src/example.py",
            "line": 10,
            "original_line": 10,
            "is_resolved": False,
            "is_outdated": False,
            "url": "https://example.invalid/thread",
            "messages": [
                {
                    "comment_id": 1,
                    "author_login": "octocat",
                    "body": "body",
                    "url": "https://example.invalid/comment",
                }
            ],
        }
    )
    failure = FailingCheck.model_validate(
        {
            "source_type": "actions_job",
            "item_id": "FAILURE-1",
            "workflow_name": "CI",
            "job_name": "lint",
            "conclusion": "failure",
            "url": "https://example.invalid/failure",
        }
    )

    monkeypatch.setitem(render_module.DEFAULT_SECTION_BUDGETS, "review_threads_section", 20)
    monkeypatch.setitem(render_module.DEFAULT_SECTION_BUDGETS, "failing_checks_section", 20)

    assert note in _render_review_thread(thread, max_chars=20)[1]
    assert (
        note
        in _render_review_threads_section(
            "Review threads", [thread], section_key="review_threads_section"
        )[1]
    )
    assert note in _render_failing_check(failure, max_chars=20)[1]
    assert note in _render_failing_checks_section([failure])[1]


def test_render_helpers_skip_none_truncation_notes(monkeypatch):
    monkeypatch.setattr(render_module, "truncate_text", lambda *args, **kwargs: ("trimmed", None))
    monkeypatch.setitem(render_module.DEFAULT_SECTION_BUDGETS, "review_threads_section", 20)
    monkeypatch.setitem(render_module.DEFAULT_SECTION_BUDGETS, "failing_checks_section", 20)

    thread = ReviewThread.model_validate(
        {
            "thread_id": "PRRT_note",
            "classifier": "review",
            "item_id": "REVIEW-1",
            "sort_key": 1,
            "path": "src/example.py",
            "line": 10,
            "original_line": 10,
            "is_resolved": False,
            "is_outdated": False,
            "url": "https://example.invalid/thread",
            "messages": [
                {
                    "comment_id": 1,
                    "author_login": "octocat",
                    "body": "body",
                    "url": "https://example.invalid/comment",
                }
            ],
        }
    )
    failure = FailingCheck.model_validate(
        {
            "source_type": "actions_job",
            "item_id": "FAILURE-1",
            "workflow_name": "CI",
            "job_name": "lint",
            "conclusion": "failure",
            "url": "https://example.invalid/failure",
        }
    )

    assert all(
        note.message != "truncated" for note in _render_review_thread(thread, max_chars=20)[1]
    )
    assert (
        _render_review_threads_section(
            "Review threads", [thread], section_key="review_threads_section"
        )[1]
        == []
    )
    assert all(
        note.message != "truncated" for note in _render_failing_check(failure, max_chars=20)[1]
    )
    assert _render_failing_checks_section([failure])[1] == []


def test_render_prompt_omits_patch_coverage_section_when_not_actionable():
    rendered = render_prompt(
        pull_request_number=17,
        head_sha="abc1234",
        review_threads=[],
        failing_checks=[],
        patch_coverage=PatchCoverageSummary(
            target_percent=100,
            actual_percent=None,
            total_changed_executable_lines=0,
            covered_changed_executable_lines=0,
            files=[],
            actionable=False,
            is_na=True,
        ),
        force_patch_coverage_section=False,
    )

    assert "# Patch coverage" not in rendered.prompt_markdown
    assert "PR head commit: abc1234" not in rendered.prompt_markdown
    assert "all clear" in rendered.prompt_markdown.lower()
    assert rendered.should_publish_comment is True


def test_render_prompt_renders_all_clear_message_when_nothing_is_actionable():
    rendered = render_prompt(
        pull_request_number=17,
        head_sha="feedface",
        review_threads=[],
        failing_checks=[],
        patch_coverage=None,
    )

    assert "PR head commit: feedface" not in rendered.prompt_markdown
    assert "all clear" in rendered.prompt_markdown.lower()
    assert "# Copilot Comments" not in rendered.prompt_markdown
    assert "# Failing Workflows" not in rendered.prompt_markdown
    assert rendered.has_actionable_items is False
    assert rendered.should_publish_comment is True


def test_render_prompt_suppresses_all_clear_comment_in_refresh_mode_by_default():
    rendered = render_prompt(
        pull_request_number=17,
        head_sha="feedface",
        review_threads=[],
        failing_checks=[],
        execution_mode="refresh",
        patch_coverage=None,
    )

    assert rendered.has_actionable_items is False
    assert rendered.should_publish_comment is False


def test_render_prompt_can_publish_all_clear_comment_in_refresh_mode_when_enabled():
    rendered = render_prompt(
        pull_request_number=17,
        head_sha="feedface",
        review_threads=[],
        failing_checks=[],
        execution_mode="refresh",
        publish_all_clear_comments_in_refresh=True,
        patch_coverage=None,
    )

    assert rendered.has_actionable_items is False
    assert rendered.should_publish_comment is True


def test_render_prompt_all_clear_notes_when_some_signal_types_are_disabled():
    rendered = render_prompt(
        pull_request_number=17,
        head_sha="feedface",
        review_threads=[],
        failing_checks=[],
        patch_coverage=None,
        include_review_comments=False,
        include_failing_checks=True,
        include_patch_coverage=False,
    )

    assert "No actionable items were found in the enabled checks" in rendered.prompt_markdown
    assert "only covers the enabled checks for this run" in rendered.prompt_markdown
    assert "Skipped checks: review comments," in rendered.prompt_markdown
    assert "patch coverage." in rendered.prompt_markdown


def test_render_prompt_ignores_disabled_signal_inputs_for_actionable_state():
    rendered = render_prompt(
        pull_request_number=17,
        head_sha="feedface",
        review_threads=[
            ReviewThread.model_validate(
                {
                    "thread_id": 1,
                    "classifier": "review",
                    "path": "src/example.py",
                    "line": 5,
                    "original_line": 5,
                    "is_resolved": False,
                    "is_outdated": False,
                    "url": "https://example.invalid/thread",
                    "item_id": "REVIEW-1",
                    "messages": [
                        {
                            "comment_id": 1,
                            "author_login": "octocat",
                            "body": "body",
                            "url": "https://example.invalid/comment",
                        }
                    ],
                }
            )
        ],
        failing_checks=[
            FailingCheck.model_validate(
                {
                    "job_id": 1,
                    "workflow_name": "CI",
                    "job_name": "smoke",
                    "url": "https://example.invalid/job",
                    "failed_steps": ["pytest"],
                    "excerpt_lines": ["failure"],
                    "item_id": "FAIL-1",
                }
            )
        ],
        patch_coverage=PatchCoverageSummary(
            target_percent=100,
            actual_percent=0,
            total_changed_executable_lines=4,
            covered_changed_executable_lines=0,
            files=[
                {
                    "path": "src/pkg/example.py",
                    "changed_added_lines": [1, 2, 3, 4],
                    "changed_executable_lines": [1, 2, 3, 4],
                    "covered_changed_executable_lines": [],
                    "uncovered_changed_executable_lines": [1, 2, 3, 4],
                    "has_measured_data": True,
                }
            ],
            actionable=True,
            is_na=False,
        ),
        include_review_comments=False,
        include_failing_checks=False,
        include_patch_coverage=False,
    )

    assert rendered.has_actionable_items is False
    assert "No actionable items were found in the enabled checks" in rendered.prompt_markdown
    assert "Skipped checks: review comments," in rendered.prompt_markdown
    assert "failing checks, patch coverage." in rendered.prompt_markdown


def test_render_prompt_forced_patch_coverage_section_is_non_actionable():
    rendered = render_prompt(
        pull_request_number=17,
        head_sha="c0ffee",
        review_threads=[],
        failing_checks=[],
        patch_coverage=PatchCoverageSummary(
            target_percent=100,
            actual_percent=None,
            total_changed_executable_lines=0,
            covered_changed_executable_lines=0,
            files=[],
            actionable=False,
            is_na=True,
        ),
        force_patch_coverage_section=True,
    )

    assert "# Patch coverage" in rendered.prompt_markdown
    assert "no changed executable Python lines" in rendered.prompt_markdown
    assert rendered.has_actionable_items is False
    assert rendered.should_publish_comment is True


def test_render_prompt_supports_custom_template_file(tmp_path):
    template = tmp_path / "template.md"
    template.write_text(
        "# PR {{ pr_number }}\n\n{{ prompt_preamble }}\n\n{{ review_comments_section }}",
        encoding="utf-8",
    )

    rendered = render_prompt(
        pull_request_number=17,
        review_threads=[
            ReviewThread.model_validate(
                {
                    "thread_id": 1,
                    "classifier": "review",
                    "path": "src/example.py",
                    "line": 5,
                    "original_line": 5,
                    "is_resolved": False,
                    "is_outdated": False,
                    "url": "https://example.invalid/thread",
                    "item_id": "REVIEW-1",
                    "messages": [
                        {
                            "comment_id": 1,
                            "author_login": "octocat",
                            "body": "body",
                            "url": "https://example.invalid/comment",
                        }
                    ],
                }
            )
        ],
        failing_checks=[],
        prompt_preamble="Repository: example",
        prompt_template_file=template,
    )

    assert rendered.template_diagnostics.template_source == "file"
    assert rendered.template_diagnostics.template_path == str(template)
    assert rendered.prompt_markdown.startswith("# PR 17")
    assert "Repository: example" in rendered.prompt_markdown
    assert "# Other Review Comments" in rendered.prompt_markdown


def test_render_prompt_rejects_unknown_template_placeholders(tmp_path):
    template = tmp_path / "template.md"
    template.write_text("{{ unsupported_placeholder }}", encoding="utf-8")

    try:
        render_prompt(
            pull_request_number=17,
            review_threads=[],
            failing_checks=[],
            prompt_template_file=template,
        )
    except ValueError as exc:
        assert "Unsupported prompt template placeholder" in str(exc)
    else:
        raise AssertionError("Expected ValueError for unsupported placeholder")


def test_render_prompt_rejects_unsupported_placeholder_syntax_variants(tmp_path):
    template = tmp_path / "template.md"
    template.write_text("{{ PR_NUMBER }} {{ pr-number }} {{ pr_number2 }}", encoding="utf-8")

    with pytest.raises(ValueError, match="Unsupported prompt template placeholder"):
        render_prompt(
            pull_request_number=17,
            review_threads=[],
            failing_checks=[],
            prompt_template_file=template,
        )


def test_render_prompt_truncates_replies_and_logs_deterministically():
    long_reply = "reply " * 500
    long_excerpt = [f"line {index}" for index in range(400)]
    rendered = render_prompt(
        pull_request_number=17,
        review_threads=[
            ReviewThread.model_validate(
                {
                    "thread_id": 1,
                    "classifier": "copilot",
                    "path": "src/example.py",
                    "line": 5,
                    "original_line": 5,
                    "is_resolved": False,
                    "is_outdated": False,
                    "url": "https://example.invalid/thread",
                    "item_id": "COPILOT-1",
                    "messages": [
                        {
                            "comment_id": 1,
                            "author_login": "copilot-pull-request-reviewer[bot]",
                            "body": "root",
                            "url": "https://example.invalid/comment",
                        },
                        {
                            "comment_id": 2,
                            "author_login": "octocat",
                            "body": long_reply,
                            "url": "https://example.invalid/comment-2",
                        },
                    ],
                }
            )
        ],
        failing_checks=[
            FailingCheck.model_validate(
                {
                    "job_id": 1,
                    "workflow_name": "CI",
                    "job_name": "smoke",
                    "url": "https://example.invalid/job",
                    "failed_steps": ["pytest"],
                    "excerpt_lines": long_excerpt,
                    "item_id": "FAIL-1",
                }
            )
        ],
    )

    serialized_notes = json.dumps(
        [note.model_dump(mode="json") for note in rendered.truncation_notes],
        sort_keys=True,
    )
    assert "[reply truncated]" in rendered.prompt_markdown
    assert "[note: excerpt truncated" in rendered.prompt_markdown
    assert "COPILOT-1" in rendered.prompt_markdown
    assert "FAIL-1" in rendered.prompt_markdown
    assert "truncate_reply_body" in serialized_notes
    assert "trim_log_excerpt" in serialized_notes


def test_load_prompt_template_defaults_to_built_in():
    template_text, template_path, template_source = load_prompt_template(None)

    assert "{{ opening_instructions }}" in template_text
    assert template_path is None
    assert template_source == "built_in"


def test_render_prompt_template_rejects_unmatched_braces():
    with pytest.raises(ValueError, match="Malformed prompt template"):
        render_prompt_template(
            template_text="{{ opening_instructions }}\n{{",
            template_source="file",
            template_path=str(Path("template.md")),
            values={
                "pr_number": "17",
                "prompt_preamble": "",
                "opening_instructions": "open",
                "copilot_comments_section": "",
                "review_comments_section": "",
                "failing_checks_section": "",
                "patch_coverage_section": "",
            },
        )


def test_render_prompt_template_allows_literal_braces_in_rendered_values():
    rendered, diagnostics = render_prompt_template(
        template_text="{{ opening_instructions }}",
        template_source="file",
        template_path="template.md",
        values={
            "pr_number": "17",
            "prompt_preamble": "",
            "opening_instructions": "Investigate literal braces {{ like this }} safely.",
            "copilot_comments_section": "",
            "review_comments_section": "",
            "failing_checks_section": "",
            "patch_coverage_section": "",
        },
    )

    assert rendered == "Investigate literal braces {{ like this }} safely."
    assert diagnostics.template_source == "file"


def test_render_prompt_template_rejects_missing_values():
    with pytest.raises(ValueError, match="Missing value\\(s\\) for prompt template placeholder"):
        render_prompt_template(
            template_text="{{ opening_instructions }}",
            template_source="file",
            template_path="template.md",
            values={
                "pr_number": "17",
                "prompt_preamble": "",
                "copilot_comments_section": "",
                "review_comments_section": "",
                "failing_checks_section": "",
                "patch_coverage_section": "",
            },
        )


def test_render_prompt_uses_safe_outer_fence_when_markdown_contains_backticks(tmp_path):
    template = tmp_path / "template.md"
    template.write_text("```python\npass\n```", encoding="utf-8")

    rendered = render_prompt(
        pull_request_number=17,
        review_threads=[],
        failing_checks=[],
        prompt_template_file=template,
    )

    assert rendered.comment_body.startswith(
        "<!-- pr-agent-context:managed-comment; schema=v5; publish_mode=append;"
    )
    assert "pr-agent-context report:\n" in rendered.comment_body
    assert "\n~~~markdown" in rendered.comment_body
    assert "\nRun metadata:\n```\nTool ref: v4\n" in rendered.comment_body
    assert "Trigger: pull request updated" in rendered.comment_body
    assert "Comment timestamp: unknown" in rendered.comment_body
    assert rendered.comment_body.endswith("\n```")


def test_build_managed_comment_body_includes_run_scoped_marker():
    body = build_managed_comment_body(
        "hello",
        pull_request_number=17,
        run_id=123,
        run_attempt=4,
        trigger_event_name="pull_request_review",
        trigger_label="review posted",
        publish_mode="append",
        head_sha="deadbeef",
        tool_ref="v4",
    )

    assert (
        body.splitlines()[0]
        == "<!-- pr-agent-context:managed-comment; schema=v5; publish_mode=append; "
        "execution_mode=ci; pr=17; head_sha=deadbeef; trigger_event=pull_request_review; "
        "generated_at=unknown; tool_ref=v4; "
        "run_id=123; run_attempt=4 -->"
    )
    assert body.splitlines()[1] == "pr-agent-context report:"
    assert "Run metadata:\n```\nTool ref: v4" in body
    assert "Trigger: review posted" in body
    assert "Comment timestamp: unknown" in body


def test_render_prompt_includes_refresh_note_when_enabled():
    rendered = render_prompt(
        pull_request_number=17,
        head_sha="deadbeef",
        run_id=1,
        run_attempt=1,
        trigger_event_name="pull_request_review",
        execution_mode="refresh",
        publish_mode="append",
        review_threads=[],
        failing_checks=[],
        include_refresh_metadata=True,
        include_patch_coverage=False,
    )

    assert rendered.prompt_markdown.startswith(
        "This is a refreshed snapshot of the current PR state."
    )


def test_wrap_markdown_code_block_chooses_unique_fence():
    wrapped = _wrap_markdown_code_block("contains ``` and ~~~ and ```` already")

    assert wrapped.startswith("`````markdown")
    assert wrapped.endswith("\n`````")


def test_render_prompt_template_inserts_preamble_when_placeholder_missing():
    rendered, diagnostics = render_prompt_template(
        template_text="# PR {{ pr_number }}\n\n{{ opening_instructions }}",
        template_source="file",
        template_path="template.md",
        values={
            "pr_number": "17",
            "prompt_preamble": "Repository: example",
            "opening_instructions": "Open instructions",
            "copilot_comments_section": "",
            "review_comments_section": "",
            "failing_checks_section": "",
            "patch_coverage_section": "",
        },
    )

    assert rendered.startswith("Repository: example\n\n# PR 17")
    assert diagnostics.prompt_preamble_inserted is True


def test_truncate_text_never_exceeds_max_chars_when_suffix_is_longer_than_budget():
    truncated, note = truncate_text(
        "abcdefghijklmnopqrstuvwxyz",
        max_chars=5,
        target="example",
        strategy="demo",
        suffix="[this suffix is longer than five]",
    )

    assert len(truncated) == 5
    assert note is not None
    assert note.message == "[this suffix is longer than five]"
    assert note.truncated_size == 5


def test_truncate_text_returns_empty_string_for_non_positive_budget():
    truncated, note = truncate_text(
        "abcdefghijklmnopqrstuvwxyz",
        max_chars=0,
        target="example",
        strategy="demo",
        suffix="[truncated]",
    )

    assert truncated == ""
    assert note is not None
    assert note.original_size == 26
    assert note.truncated_size == 0


def test_truncate_lines_reports_character_sizes_consistently():
    lines = ["alpha", "beta", "gamma", "delta"]
    truncated, note = truncate_lines(
        lines,
        max_lines=2,
        max_chars=20,
        target="example",
        strategy="demo",
        note_message="trimmed",
    )

    assert truncated == ["alpha", "beta"]
    assert note is not None
    assert note.original_size == len("alpha\nbeta\ngamma\ndelta")
    assert note.truncated_size == len("alpha\nbeta")


def test_render_review_thread_drops_metadata_and_then_truncates():
    thread = ReviewThread.model_validate(
        {
            "thread_id": 1,
            "classifier": "review",
            "path": "src/example.py",
            "line": 5,
            "original_line": 5,
            "is_resolved": False,
            "is_outdated": False,
            "url": "https://example.invalid/thread",
            "item_id": "REVIEW-1",
            "messages": [
                {
                    "comment_id": 1,
                    "author_login": "octocat",
                    "body": "root " * 400,
                    "url": "https://example.invalid/comment",
                }
            ],
        }
    )

    rendered, notes = _render_review_thread(thread, max_chars=260)

    assert "Root author:" not in rendered
    assert "[note: thread truncated to fit section budget]" in rendered
    assert {note.strategy for note in notes} >= {"drop_metadata", "section_budget"}


def test_render_review_thread_root_truncation_and_metadata_only_fallback():
    thread = ReviewThread.model_validate(
        {
            "thread_id": 2,
            "classifier": "review",
            "path": "src/example.py",
            "line": 10,
            "original_line": 10,
            "is_resolved": False,
            "is_outdated": False,
            "url": "https://example.invalid/thread-2",
            "item_id": "REVIEW-2",
            "messages": [
                {
                    "comment_id": 3,
                    "author_login": "octocat",
                    "body": "root " * 700,
                    "url": "https://example.invalid/comment-3",
                }
            ],
        }
    )

    rendered, notes = _render_review_thread(thread, max_chars=2500)

    assert "[comment truncated]" in rendered
    assert "Root author:" not in rendered
    assert "[note: thread truncated to fit section budget]" not in rendered
    assert any(note.strategy == "truncate_root_comment" for note in notes)
    assert any(note.strategy == "drop_metadata" for note in notes)


def test_render_failing_checks_section_applies_metadata_drop_and_truncation():
    failure = FailingCheck.model_validate(
        {
            "job_id": 1,
            "workflow_name": "CI",
            "job_name": "smoke",
            "matrix_label": "py311-linux",
            "url": "https://example.invalid/job",
            "failed_steps": ["pytest"],
            "excerpt_lines": [f"line {index} " + ("x" * 80) for index in range(200)],
            "item_id": "FAIL-1",
        }
    )

    rendered, notes = _render_failing_checks_section([failure])

    assert rendered.startswith("# Failing Workflows")
    assert "[note: excerpt truncated" in rendered
    assert any(note.strategy == "trim_log_excerpt" for note in notes)
    assert "line 199" not in rendered

    tiny_rendered, tiny_notes = _render_failing_check(failure, max_chars=220)
    assert "FAIL-1" in tiny_rendered
    assert any(note.strategy == "drop_metadata" for note in tiny_notes)


def test_render_failing_checks_section_supports_mixed_failure_sources():
    failures = [
        FailingCheck.model_validate(
            {
                "source_type": "actions_job",
                "workflow_name": "CI",
                "job_name": "lint",
                "run_number": 32,
                "run_attempt": 2,
                "conclusion": "failure",
                "url": "https://example.invalid/actions/lint",
                "failed_steps": ["Run ruff"],
                "excerpt_lines": ["::error::ruff failed"],
                "is_current_run": True,
                "item_id": "FAIL-1",
            }
        ),
        FailingCheck.model_validate(
            {
                "source_type": "external_check_run",
                "workflow_name": "codecov",
                "job_name": "codecov/patch",
                "app_name": "codecov",
                "status": "completed",
                "conclusion": "failure",
                "summary": "Patch coverage fell below the threshold.",
                "url": "https://example.invalid/codecov",
                "item_id": "FAIL-2",
            }
        ),
        FailingCheck.model_validate(
            {
                "source_type": "commit_status",
                "workflow_name": "Commit status",
                "job_name": "security/scan",
                "context_name": "security/scan",
                "status": "failure",
                "summary": "Dependency scan failed",
                "url": "https://example.invalid/security",
                "item_id": "FAIL-3",
            }
        ),
    ]

    rendered, notes = _render_failing_checks_section(failures)

    assert not notes
    assert rendered.startswith("# Failing Workflows")
    assert "Type: GitHub Actions job" in rendered
    assert "Current run: yes" in rendered
    assert "Type: External check run" in rendered
    assert "App: codecov" in rendered
    assert "Check: codecov/patch" in rendered
    assert "Type: Commit status" in rendered
    assert "Context: security/scan" in rendered


def test_render_failing_check_uses_not_available_placeholder_for_missing_url():
    failure = FailingCheck.model_validate(
        {
            "source_type": "commit_status",
            "workflow_name": "Commit status",
            "job_name": "security/scan",
            "context_name": "security/scan",
            "status": "failure",
            "summary": "Dependency scan failed",
            "url": "",
            "item_id": "FAIL-1",
        }
    )

    rendered, notes = _render_failing_check(failure, max_chars=2000)

    assert not notes
    assert "URL: (not available)" in rendered


def test_render_review_section_hard_caps_total_budget():
    threads = [
        ReviewThread.model_validate(
            {
                "thread_id": index,
                "classifier": "review",
                "path": f"src/example_{index}.py",
                "line": 5,
                "original_line": 5,
                "is_resolved": False,
                "is_outdated": False,
                "url": f"https://example.invalid/thread-{index}",
                "item_id": f"REVIEW-{index}",
                "messages": [
                    {
                        "comment_id": index,
                        "author_login": "octocat",
                        "body": "body " * 700,
                        "url": f"https://example.invalid/comment-{index}",
                    }
                ],
            }
        )
        for index in range(1, 12)
    ]

    rendered = render_prompt(
        pull_request_number=17,
        review_threads=threads,
        failing_checks=[],
    )

    assert len(rendered.prompt_markdown) < 20000
    assert any(note.strategy == "section_budget_cap" for note in rendered.truncation_notes)


def test_render_review_threads_section_breaks_when_budget_is_exhausted(monkeypatch):
    threads = [
        ReviewThread.model_validate(
            {
                "thread_id": index,
                "classifier": "review",
                "path": f"src/example_{index}.py",
                "line": 5,
                "original_line": 5,
                "is_resolved": False,
                "is_outdated": False,
                "url": f"https://example.invalid/thread-{index}",
                "item_id": f"REVIEW-{index}",
                "messages": [
                    {
                        "comment_id": index,
                        "author_login": "octocat",
                        "body": "body " * 80,
                        "url": f"https://example.invalid/comment-{index}",
                    }
                ],
            }
        )
        for index in range(1, 4)
    ]
    monkeypatch.setitem(render_module.DEFAULT_SECTION_BUDGETS, "review_comments_section", 1)
    monkeypatch.setattr(
        render_module,
        "_render_review_thread",
        lambda thread, max_chars: ("x" * 500, []),
    )

    rendered, notes = _render_review_threads_section(
        "Other Review Comments",
        threads,
        section_key="review_comments_section",
    )

    assert rendered
    assert any(note.strategy == "section_budget_cap" for note in notes)


def test_render_failing_checks_section_breaks_when_budget_is_exhausted(monkeypatch):
    failures = [
        FailingCheck.model_validate(
            {
                "job_id": index,
                "workflow_name": "CI",
                "job_name": f"smoke-{index}",
                "matrix_label": "py311-linux",
                "url": f"https://example.invalid/job-{index}",
                "failed_steps": ["pytest"],
                "excerpt_lines": [f"line {line} " + ("x" * 40) for line in range(30)],
                "item_id": f"FAIL-{index}",
            }
        )
        for index in range(1, 4)
    ]
    monkeypatch.setitem(render_module.DEFAULT_SECTION_BUDGETS, "failing_checks_section", 1)

    rendered, notes = _render_failing_checks_section(failures)

    assert rendered
    assert any(note.strategy == "section_budget_cap" for note in notes)


def test_render_failing_check_metadata_only_fallback_and_no_excerpt_branch():
    with_excerpt = FailingCheck.model_validate(
        {
            "job_id": 2,
            "workflow_name": "CI",
            "job_name": "smoke",
            "matrix_label": "py312-linux",
            "url": "https://example.invalid/job-2",
            "failed_steps": ["pytest"],
            "excerpt_lines": ["short excerpt"],
            "item_id": "FAIL-2",
        }
    )
    rendered, notes = _render_failing_check(with_excerpt, max_chars=150)
    assert "Matrix:" not in rendered
    assert "[note: failure details truncated to fit section budget]" not in rendered
    assert any(note.strategy == "drop_metadata" for note in notes)

    without_excerpt = FailingCheck.model_validate(
        {
            "job_id": 3,
            "workflow_name": "CI",
            "job_name": "unit",
            "url": "https://example.invalid/job-3",
            "failed_steps": [],
            "excerpt_lines": [],
            "item_id": "FAIL-3",
        }
    )
    rendered_no_excerpt, notes_no_excerpt = _render_failing_check(
        without_excerpt,
        max_chars=500,
    )
    assert "Excerpt:" not in rendered_no_excerpt
    assert notes_no_excerpt == []


def test_render_patch_coverage_section_handles_non_actionable_and_hard_limit():
    summary = PatchCoverageSummary.model_validate(
        {
            "target_percent": 100.0,
            "actual_percent": 100.0,
            "total_changed_executable_lines": 3,
            "covered_changed_executable_lines": 3,
            "files": [],
            "actionable": False,
            "is_na": False,
        }
    )
    text, notes = _render_patch_coverage_section(
        summary,
        force_patch_coverage_section=True,
    )
    assert "meeting the target" in text
    assert notes == []

    unknown_summary = PatchCoverageSummary.model_validate(
        {
            "target_percent": 100.0,
            "actual_percent": None,
            "total_changed_executable_lines": 0,
            "covered_changed_executable_lines": 0,
            "files": [],
            "actionable": True,
            "is_na": False,
        }
    )
    unknown_text, unknown_notes = _render_patch_coverage_section(
        unknown_summary,
        force_patch_coverage_section=False,
    )
    assert "could not be determined" in unknown_text
    assert unknown_notes == []

    big_summary = PatchCoverageSummary.model_validate(
        {
            "target_percent": 100.0,
            "actual_percent": 50.0,
            "total_changed_executable_lines": 4000,
            "covered_changed_executable_lines": 2000,
            "files": [
                {
                    "path": "src/ignored.py",
                    "changed_added_lines": [1, 2],
                    "changed_executable_lines": [1, 2],
                    "covered_changed_executable_lines": [1, 2],
                    "uncovered_changed_executable_lines": [],
                    "has_measured_data": True,
                },
                {
                    "path": "src/example.py",
                    "changed_added_lines": list(range(1, 10000)),
                    "changed_executable_lines": list(range(1, 10000)),
                    "covered_changed_executable_lines": [],
                    "uncovered_changed_executable_lines": list(range(1, 10000)),
                    "has_measured_data": True,
                },
            ],
            "actionable": True,
            "is_na": False,
        }
    )
    truncated_text, truncated_notes = _render_patch_coverage_section(
        big_summary,
        force_patch_coverage_section=False,
    )
    assert truncated_text.startswith("# Patch coverage")
    assert "- src/ignored.py:" not in truncated_text
    assert any(note.strategy == "hard_limit" for note in truncated_notes)


def test_render_helpers_handle_location_and_block_sanitation():
    assert _format_location(None, 3) == "unknown"
    assert _format_location("src/example.py", None) == "src/example.py"
    assert _indent_block("line1\n\nline2").splitlines()[1] == ""
    assert _sanitize_block("```py\r\npass\r\n```") == "~~~py\npass\n~~~"
    assert _wrap_markdown_code_block("plain").startswith("```markdown")
    assert _wrap_markdown_code_block("contains ``` fence").startswith("~~~markdown")
    assert _wrap_markdown_code_block("contains ``` and ~~~ fences").startswith("````markdown")


def test_render_format_percent_keeps_decimals_for_non_integral_values():
    rendered = render_prompt(
        pull_request_number=17,
        review_threads=[],
        failing_checks=[],
        patch_coverage=PatchCoverageSummary(
            target_percent=99.25,
            actual_percent=83.27,
            total_changed_executable_lines=100,
            covered_changed_executable_lines=83,
            files=[
                {
                    "path": "src/pkg/example.py",
                    "changed_added_lines": [1],
                    "changed_executable_lines": [1],
                    "covered_changed_executable_lines": [],
                    "uncovered_changed_executable_lines": [1],
                    "has_measured_data": True,
                }
            ],
            actionable=True,
            is_na=False,
        ),
    )

    assert "83.27%" in rendered.prompt_markdown
    assert "99.25%" in rendered.prompt_markdown
