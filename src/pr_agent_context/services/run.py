from __future__ import annotations

from pathlib import Path

from pr_agent_context.config import RunConfig
from pr_agent_context.github.api import GitHubApiClient
from pr_agent_context.github.issue_comments import sync_managed_comment
from pr_agent_context.github.review_threads import collect_unresolved_review_threads
from pr_agent_context.github.workflow_jobs import collect_failed_jobs
from pr_agent_context.prompt.ids import assign_item_ids
from pr_agent_context.prompt.render import render_prompt


def run_service(config: RunConfig, *, client: GitHubApiClient | None = None) -> int:
    api_client = client or GitHubApiClient(
        token=config.github_token,
        api_url=config.github_api_url,
    )
    review_threads = collect_unresolved_review_threads(
        api_client,
        owner=config.pull_request.owner,
        repo=config.pull_request.repo,
        pull_request_number=config.pull_request.number,
        max_threads=config.max_review_threads,
    )
    workflow_failures = collect_failed_jobs(
        api_client,
        owner=config.pull_request.owner,
        repo=config.pull_request.repo,
        run_id=config.run_id,
        run_attempt=config.run_attempt,
        max_failed_jobs=config.max_failed_jobs,
        max_log_lines_per_job=config.max_log_lines_per_job,
    )
    numbered_threads, numbered_failures = assign_item_ids(review_threads, workflow_failures)
    rendered = render_prompt(
        pull_request_number=config.pull_request.number,
        review_threads=numbered_threads,
        workflow_failures=numbered_failures,
        prompt_preamble=config.prompt_preamble,
    )
    publication = sync_managed_comment(
        api_client,
        owner=config.pull_request.owner,
        repo=config.pull_request.repo,
        pull_request_number=config.pull_request.number,
        body=rendered.comment_body if rendered.has_actionable_items else None,
        delete_comment_when_empty=config.delete_comment_when_empty,
        skip_comment_on_readonly_token=config.skip_comment_on_readonly_token,
    )
    if rendered.has_actionable_items:
        print(rendered.prompt_markdown)
    _write_outputs(
        config.github_output_path,
        unresolved_thread_count=len(numbered_threads),
        failed_job_count=len(numbered_failures),
        has_actionable_items=rendered.has_actionable_items,
        comment_written=publication.comment_written,
        comment_id=publication.comment_id,
        comment_url=publication.comment_url,
    )
    return 0


def _write_outputs(
    output_path: Path | None,
    *,
    unresolved_thread_count: int,
    failed_job_count: int,
    has_actionable_items: bool,
    comment_written: bool,
    comment_id: int | None,
    comment_url: str | None,
) -> None:
    if output_path is None:
        return
    lines = [
        f"unresolved_thread_count={unresolved_thread_count}",
        f"failed_job_count={failed_job_count}",
        f"has_actionable_items={str(has_actionable_items).lower()}",
        f"comment_written={str(comment_written).lower()}",
        f"comment_id={comment_id or ''}",
        f"comment_url={comment_url or ''}",
    ]
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
