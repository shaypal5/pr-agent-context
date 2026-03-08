from __future__ import annotations

import json
from pathlib import Path

import pytest

from conftest import load_json_fixture, load_text_fixture
from pr_agent_context.domain.models import PatchCoverageSummary, ReviewThread, WorkflowFailure
from pr_agent_context.prompt import render as render_module
from pr_agent_context.prompt.render import (
    _format_location,
    _indent_block,
    _render_failing_jobs_section,
    _render_patch_coverage_section,
    _render_review_thread,
    _render_review_threads_section,
    _render_workflow_failure,
    _sanitize_block,
    _wrap_markdown_code_block,
    render_prompt,
)
from pr_agent_context.prompt.template import load_prompt_template, render_prompt_template
from pr_agent_context.prompt.truncate import truncate_lines, truncate_text


def test_render_prompt_matches_expected_snapshots():
    payload = load_json_fixture("prompts/collected_context.json")
    review_threads = [ReviewThread.model_validate(item) for item in payload["review_threads"]]
    workflow_failures = [
        WorkflowFailure.model_validate(item) for item in payload["workflow_failures"]
    ]

    rendered = render_prompt(
        pull_request_number=payload["pull_request_number"],
        head_sha="def456",
        review_threads=review_threads,
        workflow_failures=workflow_failures,
        prompt_preamble=payload["prompt_preamble"],
    )

    assert rendered.prompt_markdown == load_text_fixture("prompts/expected_prompt.md").strip()
    assert rendered.comment_body == load_text_fixture("prompts/expected_comment.md").strip()
    assert len(rendered.prompt_sha256) == 64
    assert rendered.template_diagnostics.template_source == "built_in"


def test_render_prompt_renders_actionable_patch_coverage_section():
    rendered = render_prompt(
        pull_request_number=17,
        head_sha="deadbeef",
        review_threads=[],
        workflow_failures=[],
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

    assert "# Codecov/patch" in rendered.prompt_markdown
    assert "head commit deadbeef" in rendered.prompt_markdown
    assert "patch test coverage is 50%" in rendered.prompt_markdown
    assert "- src/pkg/example.py: 3, 4" in rendered.prompt_markdown
    assert rendered.has_actionable_items is True


def test_render_prompt_omits_patch_coverage_section_when_not_actionable():
    rendered = render_prompt(
        pull_request_number=17,
        head_sha="abc1234",
        review_threads=[],
        workflow_failures=[],
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

    assert "# Codecov/patch" not in rendered.prompt_markdown
    assert "head commit abc1234" in rendered.prompt_markdown
    assert "all clear" in rendered.prompt_markdown.lower()
    assert rendered.should_publish_comment is True


def test_render_prompt_renders_all_clear_message_when_nothing_is_actionable():
    rendered = render_prompt(
        pull_request_number=17,
        head_sha="feedface",
        review_threads=[],
        workflow_failures=[],
        patch_coverage=None,
    )

    assert "head commit feedface" in rendered.prompt_markdown
    assert "all clear" in rendered.prompt_markdown.lower()
    assert "# Copilot Comments" not in rendered.prompt_markdown
    assert "# Failing Jobs" not in rendered.prompt_markdown
    assert rendered.has_actionable_items is False
    assert rendered.should_publish_comment is True


def test_render_prompt_all_clear_notes_when_some_signal_types_are_disabled():
    rendered = render_prompt(
        pull_request_number=17,
        head_sha="feedface",
        review_threads=[],
        workflow_failures=[],
        patch_coverage=None,
        include_review_comments=False,
        include_failing_jobs=True,
        include_patch_coverage=False,
    )

    assert "No actionable items were found in the enabled checks" in rendered.prompt_markdown
    assert "only covers the enabled checks for this run" in rendered.prompt_markdown
    assert "Skipped checks: review comments, patch coverage." in rendered.prompt_markdown


def test_render_prompt_forced_patch_coverage_section_is_non_actionable():
    rendered = render_prompt(
        pull_request_number=17,
        head_sha="c0ffee",
        review_threads=[],
        workflow_failures=[],
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

    assert "# Codecov/patch" in rendered.prompt_markdown
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
        workflow_failures=[],
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
            workflow_failures=[],
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
            workflow_failures=[],
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
        workflow_failures=[
            WorkflowFailure.model_validate(
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
                "failing_jobs_section": "",
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
            "failing_jobs_section": "",
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
                "failing_jobs_section": "",
                "patch_coverage_section": "",
            },
        )


def test_render_prompt_uses_safe_outer_fence_when_markdown_contains_backticks(tmp_path):
    template = tmp_path / "template.md"
    template.write_text("```python\npass\n```", encoding="utf-8")

    rendered = render_prompt(
        pull_request_number=17,
        review_threads=[],
        workflow_failures=[],
        prompt_template_file=template,
    )

    assert rendered.comment_body.startswith(
        "<!-- pr-agent-context:managed-comment -->\n~~~markdown"
    )
    assert rendered.comment_body.endswith("\n~~~")


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
            "failing_jobs_section": "",
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


def test_render_failing_jobs_section_applies_metadata_drop_and_truncation():
    failure = WorkflowFailure.model_validate(
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

    rendered, notes = _render_failing_jobs_section([failure])

    assert rendered.startswith("# Failing Jobs")
    assert "[note: excerpt truncated" in rendered
    assert any(note.strategy == "trim_log_excerpt" for note in notes)
    assert "line 199" not in rendered

    tiny_rendered, tiny_notes = _render_workflow_failure(failure, max_chars=220)
    assert "FAIL-1" in tiny_rendered
    assert any(note.strategy == "drop_metadata" for note in tiny_notes)


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
        workflow_failures=[],
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


def test_render_failing_jobs_section_breaks_when_budget_is_exhausted(monkeypatch):
    failures = [
        WorkflowFailure.model_validate(
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
    monkeypatch.setitem(render_module.DEFAULT_SECTION_BUDGETS, "failing_jobs_section", 1)

    rendered, notes = _render_failing_jobs_section(failures)

    assert rendered
    assert any(note.strategy == "section_budget_cap" for note in notes)


def test_render_workflow_failure_metadata_only_fallback_and_no_excerpt_branch():
    with_excerpt = WorkflowFailure.model_validate(
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
    rendered, notes = _render_workflow_failure(with_excerpt, max_chars=120)
    assert "Matrix:" not in rendered
    assert "[note: failure details truncated to fit section budget]" not in rendered
    assert any(note.strategy == "drop_metadata" for note in notes)

    without_excerpt = WorkflowFailure.model_validate(
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
    rendered_no_excerpt, notes_no_excerpt = _render_workflow_failure(
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
    assert truncated_text.startswith("# Codecov/patch")
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
        workflow_failures=[],
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
