from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from pr_agent_context import __version__
from pr_agent_context.config import RunConfig
from pr_agent_context.coverage.artifacts import resolve_coverage_files
from pr_agent_context.coverage.combine import build_combined_coverage
from pr_agent_context.coverage.git_diff import collect_changed_lines
from pr_agent_context.coverage.patch import (
    compute_patch_coverage,
    compute_patch_coverage_from_xml_reports,
    describe_patch_coverage_scope,
)
from pr_agent_context.domain.models import (
    CollectedContext,
    DebugSummary,
    PatchCoverageSummary,
)
from pr_agent_context.github.api import GitHubApiClient
from pr_agent_context.github.failing_checks import collect_failing_checks
from pr_agent_context.github.issue_comments import sync_managed_comment
from pr_agent_context.github.pull_request_context import resolve_pull_request_ref
from pr_agent_context.github.review_threads import (
    collect_unresolved_review_threads,
    wait_for_review_threads_to_settle,
)
from pr_agent_context.prompt.ids import assign_item_ids
from pr_agent_context.prompt.render import render_prompt


def run_service(config: RunConfig, *, client: GitHubApiClient | None = None) -> int:
    generated_at = datetime.now(timezone.utc).isoformat()
    api_client = client or GitHubApiClient(
        token=config.github_token,
        api_url=config.github_api_url,
    )
    repository_owner = config.repository_owner or (
        config.pull_request.owner if config.pull_request is not None else ""
    )
    repository_name = config.repository_name or (
        config.pull_request.repo if config.pull_request is not None else ""
    )
    pull_request, pull_request_debug = resolve_pull_request_ref(
        api_client,
        owner=repository_owner,
        repo=repository_name,
        trigger=config.trigger,
        pull_request_hint=config.pull_request,
    )
    _log(
        "start",
        version=__version__,
        tool_ref=config.tool_ref,
        repository=f"{pull_request.owner}/{pull_request.repo}",
        pull_request_number=pull_request.number,
        base_sha=pull_request.base_sha,
        head_sha=pull_request.head_sha,
        run_id=config.run_id,
        run_attempt=config.run_attempt,
        execution_mode=config.execution_mode,
        publish_mode=config.publish_mode,
        trigger_event_name=config.trigger.event_name,
        trigger_action=config.trigger.action or "",
    )
    _log(
        "config",
        include_refresh_metadata=config.include_refresh_metadata,
        include_review_comments=config.include_review_comments,
        include_failing_checks=config.include_failing_checks,
        include_cross_run_failures=config.include_cross_run_failures,
        include_external_checks=config.include_external_checks,
        wait_for_checks_to_settle=config.wait_for_checks_to_settle,
        wait_for_reviews_to_settle=config.wait_for_reviews_to_settle,
        publish_all_clear_comments_in_refresh=config.publish_all_clear_comments_in_refresh,
        include_patch_coverage=config.include_patch_coverage,
        patch_coverage_source_mode=config.patch_coverage_source_mode,
        coverage_report_artifact_name=config.coverage_report_artifact_name,
        coverage_report_filename=config.coverage_report_filename,
        enable_cross_run_coverage_lookup=config.enable_cross_run_coverage_lookup,
        delete_comment_when_empty=config.delete_comment_when_empty,
        skip_comment_on_readonly_token=config.skip_comment_on_readonly_token,
        prompt_template_file=(
            str(config.prompt_template_file) if config.prompt_template_file else ""
        ),
        coverage_source_workflows=list(config.coverage_source_workflows),
        coverage_source_conclusions=list(config.coverage_source_conclusions),
        coverage_selection_strategy=config.coverage_selection_strategy,
        fork_behavior=config.fork_behavior,
        check_settle_timeout_seconds=config.check_settle_timeout_seconds,
        check_settle_poll_interval_seconds=config.check_settle_poll_interval_seconds,
        review_settle_timeout_seconds=config.review_settle_timeout_seconds,
        review_settle_poll_interval_seconds=config.review_settle_poll_interval_seconds,
        characters_per_line=config.characters_per_line,
        max_actions_runs=config.max_actions_runs,
        max_external_checks=config.max_external_checks,
        max_failing_checks=config.max_failing_checks,
    )
    _log(
        "trigger",
        event_name=config.trigger.event_name,
        action=config.trigger.action or "",
        source=config.trigger.source,
        trigger_pull_request_number=config.trigger.pull_request_number or "",
        trigger_head_sha=config.trigger.head_sha or "",
        trigger_is_fork=config.trigger.is_fork if config.trigger.is_fork is not None else "",
        pull_request_resolution=pull_request_debug.get("resolution", ""),
    )

    review_threads = []
    review_settlement_debug = {
        "enabled": False,
        "settled": False,
        "timed_out": False,
        "skipped_reason": "disabled",
        "poll_count": 0,
        "elapsed_seconds": 0.0,
        "thread_count": 0,
    }
    if config.include_review_comments:
        if config.execution_mode == "refresh" and config.wait_for_reviews_to_settle:
            review_threads, review_settlement_debug = wait_for_review_threads_to_settle(
                api_client,
                owner=pull_request.owner,
                repo=pull_request.repo,
                pull_request_number=pull_request.number,
                max_threads=config.max_review_threads,
                copilot_matcher=config.copilot_author_patterns,
                timeout_seconds=config.review_settle_timeout_seconds,
                poll_interval_seconds=config.review_settle_poll_interval_seconds,
            )
        else:
            review_threads = collect_unresolved_review_threads(
                api_client,
                owner=pull_request.owner,
                repo=pull_request.repo,
                pull_request_number=pull_request.number,
                max_threads=config.max_review_threads,
                copilot_matcher=config.copilot_author_patterns,
            )
            if config.execution_mode == "refresh":
                review_settlement_debug = {
                    **review_settlement_debug,
                    "enabled": config.wait_for_reviews_to_settle,
                    "skipped_reason": "refresh_wait_disabled",
                    "thread_count": len(review_threads),
                }
    _log("review_threads", enabled=config.include_review_comments, count=len(review_threads))
    _log(
        "review_settlement",
        enabled=review_settlement_debug.get("enabled", False),
        settled=review_settlement_debug.get("settled", False),
        timed_out=review_settlement_debug.get("timed_out", False),
        skipped_reason=review_settlement_debug.get("skipped_reason", ""),
        poll_count=review_settlement_debug.get("poll_count", 0),
        elapsed_seconds=review_settlement_debug.get("elapsed_seconds", 0.0),
        thread_count=review_settlement_debug.get("thread_count", 0),
    )

    failing_checks = []
    failing_check_debug: dict | None = None
    if config.include_failing_checks:
        failing_checks, failing_check_debug = collect_failing_checks(
            api_client,
            owner=pull_request.owner,
            repo=pull_request.repo,
            head_sha=pull_request.head_sha,
            current_run_id=config.run_id,
            current_run_attempt=config.run_attempt,
            include_cross_run_failures=config.include_cross_run_failures,
            include_external_checks=config.include_external_checks,
            wait_for_checks_to_settle=config.wait_for_checks_to_settle,
            suppress_codecov_checks=(
                config.include_patch_coverage
                and config.patch_coverage_source_mode == "coverage_xml_artifact"
            ),
            max_actions_runs=config.max_actions_runs,
            max_actions_jobs=config.max_actions_jobs,
            max_external_checks=config.max_external_checks,
            max_failing_checks=config.max_failing_checks,
            max_log_lines_per_job=config.max_log_lines_per_job,
            check_settle_timeout_seconds=config.check_settle_timeout_seconds,
            check_settle_poll_interval_seconds=config.check_settle_poll_interval_seconds,
        )
    _log(
        "check_settlement",
        enabled=(failing_check_debug or {}).get("settlement", {}).get("enabled", False),
        settled=(failing_check_debug or {}).get("settlement", {}).get("settled", False),
        timed_out=(failing_check_debug or {}).get("settlement", {}).get("timed_out", False),
        poll_count=(failing_check_debug or {}).get("settlement", {}).get("poll_count", 0),
        elapsed_seconds=(failing_check_debug or {}).get("settlement", {}).get("elapsed_seconds", 0),
        pending_count=(failing_check_debug or {}).get("settlement", {}).get("pending_count", 0),
        skipped_reason=(failing_check_debug or {}).get("settlement", {}).get("skipped_reason", ""),
        warning_count=len((failing_check_debug or {}).get("settlement", {}).get("warnings", [])),
    )
    _log(
        "failing_checks",
        enabled=config.include_failing_checks,
        count=len(failing_checks),
        source_counts=(failing_check_debug or {}).get("deduped_source_counts", {}),
        warning_count=len((failing_check_debug or {}).get("warnings", [])),
    )

    patch_coverage = None
    changed_lines: dict[str, list[int]] = {}
    coverage_files: list[Path] = []
    coverage_source_debug: dict | None = None
    if config.include_patch_coverage:
        coverage_working_directory = str(config.workspace.resolve())
        process_cwd = str(Path.cwd().resolve())
        changed_lines = collect_changed_lines(
            config.workspace,
            base_sha=pull_request.base_sha,
            head_sha=pull_request.head_sha,
        )
        coverage_files, coverage_source_debug = resolve_coverage_files(
            client=api_client,
            owner=pull_request.owner,
            repo=pull_request.repo,
            head_sha=pull_request.head_sha,
            local_artifacts_dir=config.coverage_artifacts_dir,
            patch_coverage_source_mode=config.patch_coverage_source_mode,
            artifact_prefix=config.coverage_artifact_prefix,
            coverage_report_artifact_name=config.coverage_report_artifact_name,
            coverage_report_filename=config.coverage_report_filename,
            enable_cross_run_lookup=config.enable_cross_run_coverage_lookup,
            execution_mode=config.execution_mode,
            workflow_names=config.coverage_source_workflows,
            allowed_conclusions=config.coverage_source_conclusions,
            selection_strategy=config.coverage_selection_strategy,
            max_candidate_runs=config.max_actions_runs,
        )
        coverage_source_pending = bool((coverage_source_debug or {}).get("coverage_source_pending"))
        _log(
            "patch_inputs",
            enabled=config.include_patch_coverage,
            patch_coverage_source_mode=config.patch_coverage_source_mode,
            changed_files=len(changed_lines),
            changed_python_files=sum(1 for path in changed_lines if path.endswith(".py")),
            coverage_artifact_files=len(coverage_files),
        )
        _log(
            "coverage_source",
            resolution=(coverage_source_debug or {}).get("resolution", ""),
            candidate_runs=len((coverage_source_debug or {}).get("candidate_runs", [])),
            selected_run_id=((coverage_source_debug or {}).get("selected_run") or {}).get("id", ""),
            selected_artifact_count=len(
                (coverage_source_debug or {}).get("selected_artifacts", [])
            ),
            warning_count=len((coverage_source_debug or {}).get("warnings", [])),
        )
        if config.patch_coverage_source_mode == "coverage_xml_artifact":
            patch_coverage, patch_scope_debug = compute_patch_coverage_from_xml_reports(
                workspace=config.workspace,
                changed_lines_by_file=changed_lines,
                report_files=coverage_files,
                target_percent=config.target_patch_coverage,
            )
        else:
            combined_coverage = build_combined_coverage(
                workspace=config.workspace,
                coverage_files=coverage_files,
            )
            patch_scope_debug = describe_patch_coverage_scope(
                workspace=config.workspace,
                coverage=combined_coverage,
                has_coverage_artifacts=bool(coverage_files),
                coverage_source_pending=coverage_source_pending,
            )
            patch_coverage = compute_patch_coverage(
                workspace=config.workspace,
                changed_lines_by_file=changed_lines,
                coverage=combined_coverage,
                target_percent=config.target_patch_coverage,
                has_coverage_artifacts=bool(coverage_files),
                coverage_source_pending=coverage_source_pending,
            )
        patch_scope_warnings = list(patch_scope_debug.get("warnings", []))
        for warning in patch_scope_debug.get("scope_warnings", []):
            if warning not in patch_scope_warnings:
                patch_scope_warnings.append(warning)

        coverage_source_debug = {
            **(coverage_source_debug or {}),
            "coverage_working_directory": coverage_working_directory,
            "coverage_working_directory_mode": (
                "process_cwd_matches_workspace"
                if coverage_working_directory == process_cwd
                else "workspace_override"
            ),
            "process_cwd": process_cwd,
            "combined_measured_file_count": patch_scope_debug["measured_file_count"],
            "combined_measured_file_sample": patch_scope_debug["measured_file_sample"],
            "coverage_source_pending": patch_scope_debug.get(
                "coverage_source_pending",
                coverage_source_pending,
            ),
            "inferred_source_roots": patch_scope_debug["inferred_source_roots"],
            "patch_scope_strategy": patch_scope_debug["scope_strategy"],
            "explicit_source": patch_scope_debug["explicit_source"],
            "explicit_source_pkgs": patch_scope_debug["explicit_source_pkgs"],
            "patch_scope_warnings": patch_scope_warnings,
            "codecov_suppressed": config.patch_coverage_source_mode == "coverage_xml_artifact",
        }
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

    numbered_threads, numbered_failures = assign_item_ids(review_threads, failing_checks)
    collected_context = CollectedContext(
        trigger=config.trigger,
        pull_request=pull_request,
        review_threads=numbered_threads,
        failing_checks=numbered_failures,
        patch_coverage=patch_coverage,
        failing_check_debug=failing_check_debug,
        review_settlement_debug=review_settlement_debug,
        coverage_source_debug=coverage_source_debug,
    )
    rendered = render_prompt(
        pull_request_number=pull_request.number,
        head_sha=pull_request.head_sha,
        run_id=config.run_id,
        run_attempt=config.run_attempt,
        trigger_event_name=config.trigger.event_name,
        trigger_label=config.trigger.label or config.trigger.source,
        execution_mode=config.execution_mode,
        publish_mode=config.publish_mode,
        tool_ref=config.tool_ref,
        tool_version=__version__,
        review_threads=numbered_threads,
        failing_checks=numbered_failures,
        patch_coverage=patch_coverage,
        include_review_comments=config.include_review_comments,
        include_failing_checks=config.include_failing_checks,
        include_patch_coverage=config.include_patch_coverage,
        include_refresh_metadata=config.include_refresh_metadata,
        publish_all_clear_comments_in_refresh=config.publish_all_clear_comments_in_refresh,
        prompt_preamble=config.prompt_preamble,
        force_patch_coverage_section=config.force_patch_coverage_section,
        prompt_template_file=config.prompt_template_file,
        characters_per_line=config.characters_per_line,
        generated_at=generated_at,
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
        owner=pull_request.owner,
        repo=pull_request.repo,
        pull_request_number=pull_request.number,
        run_id=config.run_id,
        run_attempt=config.run_attempt,
        head_sha=pull_request.head_sha,
        tool_ref=config.tool_ref,
        trigger_event_name=config.trigger.event_name,
        execution_mode=config.execution_mode,
        publish_mode=config.publish_mode,
        generated_at=generated_at,
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
        publish_mode=publication.publish_mode,
        managed_comments=publication.managed_comment_count,
        body_changed=publication.body_changed,
        matched_existing_comment=publication.matched_existing_comment,
        matched_comment_id=publication.matched_comment_id or "",
        matched_comment_run_id=publication.matched_comment_run_id or "",
        matched_comment_run_attempt=publication.matched_comment_run_attempt or "",
        skipped_reason=publication.skipped_reason or "",
        error_status_code=publication.error_status_code or "",
    )
    if rendered.has_actionable_items:
        print(rendered.prompt_markdown)

    summary = DebugSummary(
        tool_ref=config.tool_ref,
        unresolved_thread_count=len(numbered_threads),
        failing_check_count=len(numbered_failures),
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
        failing_check_count=summary.failing_check_count,
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
            publication=publication,
            failing_check_debug=failing_check_debug,
            coverage_source_debug=coverage_source_debug,
            pull_request_debug=pull_request_debug,
        )
    _write_outputs(
        config.github_output_path,
        unresolved_thread_count=len(numbered_threads),
        failing_check_count=len(numbered_failures),
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
    failing_check_count: int,
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
        f"failing_check_count={failing_check_count}",
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
    publication,
    failing_check_debug: dict | None,
    coverage_source_debug: dict | None,
    pull_request_debug: dict[str, object],
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
    if coverage_source_debug is not None:
        _write_json(debug_dir / "coverage-source.json", coverage_source_debug)
    _write_json(debug_dir / "pull-request-context.json", pull_request_debug)
    _write_json(debug_dir / "comment-sync.json", publication.model_dump(mode="json"))


def _write_json(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _log(event: str, **fields: object) -> None:
    payload: dict[str, object] = {"tool": "pr-agent-context", "event": event}
    for key, value in fields.items():
        if value == "":
            continue
        payload[key] = value
    print(json.dumps(payload, sort_keys=True))
