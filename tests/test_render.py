from __future__ import annotations

import json

from conftest import load_json_fixture, load_text_fixture
from pr_agent_context.domain.models import PatchCoverageSummary, ReviewThread, WorkflowFailure
from pr_agent_context.prompt.render import render_prompt


def test_render_prompt_matches_expected_snapshots():
    payload = load_json_fixture("prompts/collected_context.json")
    review_threads = [ReviewThread.model_validate(item) for item in payload["review_threads"]]
    workflow_failures = [
        WorkflowFailure.model_validate(item) for item in payload["workflow_failures"]
    ]

    rendered = render_prompt(
        pull_request_number=payload["pull_request_number"],
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
    assert "patch test coverage is 50%" in rendered.prompt_markdown
    assert "- src/pkg/example.py: 3, 4" in rendered.prompt_markdown
    assert rendered.has_actionable_items is True


def test_render_prompt_omits_patch_coverage_section_when_not_actionable():
    rendered = render_prompt(
        pull_request_number=17,
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
    assert rendered.should_publish_comment is False


def test_render_prompt_forced_patch_coverage_section_is_non_actionable():
    rendered = render_prompt(
        pull_request_number=17,
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
