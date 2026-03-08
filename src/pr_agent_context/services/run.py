from __future__ import annotations

import json
from pathlib import Path

from pr_agent_context import __version__
from pr_agent_context.config import RunConfig
from pr_agent_context.coverage.artifacts import discover_coverage_files
from pr_agent_context.coverage.combine import build_combined_coverage
from pr_agent_context.coverage.git_diff import collect_changed_lines
from pr_agent_context.coverage.patch import compute_patch_coverage
from pr_agent_context.domain.models import (
    CollectedContext,
    DebugSummary,
    PatchCoverageSummary,
)
from pr_agent_context.github.api import GitHubApiClient
from pr_agent_context.github.failing_checks import collect_failing_checks
from pr_agent_context.github.issue_comments import sync_managed_comment
from pr_agent_context.github.review_threads import collect_unresolved_review_threads
from pr_agent_context.prompt.ids import assign_item_ids
from pr_agent_context.prompt.render import render_prompt


def run_service(config: RunConfig, *, client: GitHubApiClient | None = None) -> int:
    _log(
        "start",
        version=__version__,
        tool_ref=config.tool_ref,
        repository=f"{config.pull_request.owner}/{config.pull_request.repo}",
        pull_request_number=config.pull_request.number,
        base_sha=config.pull_request.base_sha,
        head_sha=config.pull_request.head_sha,
        run_id=config.run_id,
        run_attempt=config.run_attempt,
    )
    _log(
        "config",
        include_review_comments=config.include_review_comments,
        include_failing_jobs=config.include_failing_jobs,
        include_cross_run_failures=config.include_cross_run_failures,
        include_external_checks=config.include_external_checks,
        include_patch_coverage=config.include_patch_coverage,
        delete_comment_when_empty=config.delete_comment_when_empty,
        skip_comment_on_readonly_token=config.skip_comment_on_readonly_token,
        prompt_template_file=(
            str(config.prompt_template_file) if config.prompt_template_file else ""
        ),
        characters_per_line=config.characters_per_line,
        max_failed_runs=config.max_failed_runs,
        max_external_checks=config.max_external_checks,
        max_failing_items=config.max_failing_items,
    )
    api_client = client or GitHubApiClient(
        token=config.github_token,
        api_url=config.github_api_url,
    )

    review_threads = []
    if config.include_review_comments:
        review_threads = collect_unresolved_review_threads(
            api_client,
            owner=config.pull_request.owner,
            repo=config.pull_request.repo,
            pull_request_number=config.pull_request.number,
            max_threads=config.max_review_threads,
            copilot_matcher=config.copilot_author_patterns,
        )
    _log("review_threads", enabled=config.include_review_comments, count=len(review_threads))

    workflow_failures = []
    failing_check_debug: dict | None = None
    if config.include_failing_jobs:
        workflow_failures, failing_check_debug = collect_failing_checks(
            api_client,
            owner=config.pull_request.owner,
            repo=config.pull_request.repo,
            head_sha=config.pull_request.head_sha,
            current_run_id=config.run_id,
            current_run_attempt=config.run_attempt,
            include_cross_run_failures=config.include_cross_run_failures,
            include_external_checks=config.include_external_checks,
            max_failed_runs=config.max_failed_runs,
            max_failed_jobs=config.max_failed_jobs,
            max_external_checks=config.max_external_checks,
            max_failing_items=config.max_failing_items,
            max_log_lines_per_job=config.max_log_lines_per_job,
        )
    _log(
        "workflow_failures",
        enabled=config.include_failing_jobs,
        count=len(workflow_failures),
        source_counts=(failing_check_debug or {}).get("deduped_source_counts", {}),
        warning_count=len((failing_check_debug or {}).get("warnings", [])),
    )

    patch_coverage = None
    changed_lines: dict[str, list[int]] = {}
    coverage_files: list[Path] = []
    if config.include_patch_coverage:
        changed_lines = collect_changed_lines(
            config.workspace,
            base_sha=config.pull_request.base_sha,
            head_sha=config.pull_request.head_sha,
        )
        coverage_files = discover_coverage_files(config.coverage_artifacts_dir)
        _log(
            "patch_inputs",
            enabled=config.include_patch_coverage,
            changed_files=len(changed_lines),
            changed_python_files=sum(1 for path in changed_lines if path.endswith(".py")),
            coverage_artifact_files=len(coverage_files),
        )
        combined_coverage = build_combined_coverage(
            workspace=config.workspace,
            coverage_files=coverage_files,
        )
        patch_coverage = compute_patch_coverage(
            workspace=config.workspace,
            changed_lines_by_file=changed_lines,
            coverage=combined_coverage,
            target_percent=config.target_patch_coverage,
        )
        _log(
            "patch_result",
            is_na=patch_coverage.is_na,
            actionable=patch_coverage.actionable,
            actual_percent=(
                ""
                if patch_coverage.actual_percent is None
                else round(patch_coverage.actual_percent, 2)
            ),
            target_percent=config.target_patch_coverage,
            total_changed_executable_lines=patch_coverage.total_changed_executable_lines,
            covered_changed_executable_lines=patch_coverage.covered_changed_executable_lines,
            uncovered_files=len(patch_coverage.files),
        )
    else:
        _log(
            "patch_inputs",
            enabled=False,
            changed_files=0,
            changed_python_files=0,
            coverage_artifact_files=0,
        )

    numbered_threads, numbered_failures = assign_item_ids(review_threads, workflow_failures)
    collected_context = CollectedContext(
        pull_request=config.pull_request,
        review_threads=numbered_threads,
        workflow_failures=numbered_failures,
        patch_coverage=patch_coverage,
        failing_check_debug=failing_check_debug,
    )
    rendered = render_prompt(
        pull_request_number=config.pull_request.number,
        head_sha=config.pull_request.head_sha,
        review_threads=numbered_threads,
        workflow_failures=numbered_failures,
        patch_coverage=patch_coverage,
        include_review_comments=config.include_review_comments,
        include_failing_jobs=config.include_failing_jobs,
        include_patch_coverage=config.include_patch_coverage,
        prompt_preamble=config.prompt_preamble,
        force_patch_coverage_section=config.force_patch_coverage_section,
        prompt_template_file=config.prompt_template_file,
        characters_per_line=config.characters_per_line,
    )
    _log(
        "render",
        prompt_sha256=rendered.prompt_sha256,
        has_actionable_items=rendered.has_actionable_items,
        should_publish_comment=rendered.should_publish_comment,
        truncation_count=len(rendered.truncation_notes),
        template_source=rendered.template_diagnostics.template_source,
        template_path=rendered.template_diagnostics.template_path or "",
    )
    publication = sync_managed_comment(
        api_client,
        owner=config.pull_request.owner,
        repo=config.pull_request.repo,
        pull_request_number=config.pull_request.number,
        body=rendered.comment_body if rendered.should_publish_comment else None,
        delete_comment_when_empty=config.delete_comment_when_empty,
        skip_comment_on_readonly_token=config.skip_comment_on_readonly_token,
    )
    _log(
        "comment_sync",
        action=publication.action,
        comment_written=publication.comment_written,
        comment_id=publication.comment_id or "",
        comment_url=publication.comment_url or "",
        existing_managed_comments=publication.existing_managed_comment_count,
        duplicate_managed_comments=publication.duplicate_managed_comment_count,
        body_changed=publication.body_changed,
        skipped_reason=publication.skipped_reason or "",
        error_status_code=publication.error_status_code or "",
    )
    if rendered.has_actionable_items:
        print(rendered.prompt_markdown)

    summary = DebugSummary(
        tool_ref=config.tool_ref,
        unresolved_thread_count=len(numbered_threads),
        failed_job_count=len(numbered_failures),
        failing_check_source_counts=(failing_check_debug or {}).get("deduped_source_counts", {}),
        patch_coverage_percent=_patch_coverage_percent(patch_coverage),
        has_actionable_items=rendered.has_actionable_items,
        should_publish_comment=rendered.should_publish_comment,
        comment_written=publication.comment_written,
        comment_id=publication.comment_id,
        comment_url=publication.comment_url,
        prompt_sha256=rendered.prompt_sha256,
        truncation_count=len(rendered.truncation_notes),
    )
    _log(
        "summary",
        unresolved_thread_count=summary.unresolved_thread_count,
        failed_job_count=summary.failed_job_count,
        failing_check_source_counts=summary.failing_check_source_counts,
        patch_coverage_percent=""
        if summary.patch_coverage_percent is None
        else round(summary.patch_coverage_percent, 2),
        has_actionable_items=summary.has_actionable_items,
        comment_written=summary.comment_written,
        prompt_sha256=summary.prompt_sha256,
    )
    if config.debug_artifacts:
        _write_debug_artifacts(
            config.debug_artifacts_dir,
            collected_context=collected_context,
            rendered=rendered,
            summary=summary,
            failing_check_debug=failing_check_debug,
        )
    _write_outputs(
        config.github_output_path,
        unresolved_thread_count=len(numbered_threads),
        failed_job_count=len(numbered_failures),
        has_actionable_items=rendered.has_actionable_items,
        patch_coverage_percent=_patch_coverage_percent(patch_coverage),
        comment_written=publication.comment_written,
        comment_id=publication.comment_id,
        comment_url=publication.comment_url,
        prompt_sha256=rendered.prompt_sha256,
    )
    return 0


def _write_outputs(
    output_path: Path | None,
    *,
    unresolved_thread_count: int,
    failed_job_count: int,
    has_actionable_items: bool,
    patch_coverage_percent: float | None,
    comment_written: bool,
    comment_id: int | None,
    comment_url: str | None,
    prompt_sha256: str,
) -> None:
    if output_path is None:
        return
    lines = [
        f"unresolved_thread_count={unresolved_thread_count}",
        f"failed_job_count={failed_job_count}",
        f"has_actionable_items={str(has_actionable_items).lower()}",
        "patch_coverage_percent="
        f"{'' if patch_coverage_percent is None else round(patch_coverage_percent, 2)}",
        f"comment_written={str(comment_written).lower()}",
        f"comment_id={comment_id or ''}",
        f"comment_url={comment_url or ''}",
        f"prompt_sha256={prompt_sha256 or ''}",
    ]
    output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _patch_coverage_percent(patch_coverage: PatchCoverageSummary | None) -> float | None:
    if patch_coverage is None or patch_coverage.actual_percent is None:
        return None
    return patch_coverage.actual_percent


def _write_debug_artifacts(
    debug_dir: Path | None,
    *,
    collected_context: CollectedContext,
    rendered,
    summary: DebugSummary,
    failing_check_debug: dict | None,
) -> None:
    if debug_dir is None:
        return
    debug_dir.mkdir(parents=True, exist_ok=True)
    _write_json(debug_dir / "collected-context.json", collected_context.model_dump(mode="json"))
    (debug_dir / "prompt.md").write_text(rendered.prompt_markdown + "\n", encoding="utf-8")
    (debug_dir / "comment-body.md").write_text(rendered.comment_body + "\n", encoding="utf-8")
    _write_json(
        debug_dir / "summary.json",
        {
            **summary.model_dump(mode="json"),
            "template_diagnostics": rendered.template_diagnostics.model_dump(mode="json"),
            "truncation_notes": [
                note.model_dump(mode="json") for note in rendered.truncation_notes
            ],
        },
    )
    if failing_check_debug is not None:
        _write_json(debug_dir / "failing-check-universe.json", failing_check_debug)


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _log(event: str, **fields: object) -> None:
    payload: dict[str, object] = {"tool": "pr-agent-context", "event": event}
    for key, value in fields.items():
        if value == "":
            continue
        payload[key] = value
    print(json.dumps(payload, sort_keys=True))
