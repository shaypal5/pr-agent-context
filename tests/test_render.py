from __future__ import annotations

from conftest import load_json_fixture, load_text_fixture
from pr_agent_context.domain.models import ReviewThread, WorkflowFailure
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
