from __future__ import annotations

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
