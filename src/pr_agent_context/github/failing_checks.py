from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime

from pr_agent_context.constants import (
    ACTIONS_APP_NAMES,
    FAILED_CHECK_CONCLUSIONS,
    FAILED_JOB_CONCLUSIONS,
    FAILED_STATUS_STATES,
)
from pr_agent_context.domain.models import FailingCheck, failing_check_sort_key
from pr_agent_context.github.api import GitHubApiClient, GitHubApiError
from pr_agent_context.github.workflow_jobs import (
    FAILED_STEP_CONCLUSIONS,
    extract_log_text,
    split_job_display_name,
    trim_log_excerpt,
)

_monotonic = time.monotonic
_sleep = time.sleep


def collect_failing_checks(
    client: GitHubApiClient,
    *,
    owner: str,
    repo: str,
    head_sha: str,
    current_run_id: int,
    current_run_attempt: int,
    include_cross_run_failures: bool,
    include_external_checks: bool,
    wait_for_checks_to_settle: bool,
    max_actions_runs: int,
    max_actions_jobs: int,
    max_external_checks: int,
    max_failing_checks: int,
    max_log_lines_per_job: int,
    check_settle_timeout_seconds: int,
    check_settle_poll_interval_seconds: int,
) -> tuple[list[FailingCheck], dict[str, object]]:
    warnings: list[str] = []
    raw_failures: list[FailingCheck] = []
    settlement = {
        "enabled": False,
        "settled": False,
        "timed_out": False,
        "poll_count": 0,
        "elapsed_seconds": 0.0,
        "min_wait_seconds": 0,
        "pending_count": 0,
        "snapshot_count": 0,
        "skipped_reason": "disabled",
        "warnings": [],
    }

    if wait_for_checks_to_settle and check_settle_timeout_seconds <= 0:
        settlement = {
            **settlement,
            "enabled": True,
            "skipped_reason": "timeout_non_positive",
        }
    elif wait_for_checks_to_settle and check_settle_poll_interval_seconds <= 0:
        settlement = {
            **settlement,
            "enabled": True,
            "skipped_reason": "poll_interval_non_positive",
        }
    elif wait_for_checks_to_settle and (include_cross_run_failures or include_external_checks):
        settlement, settlement_warnings = _wait_for_check_settlement(
            client,
            owner=owner,
            repo=repo,
            head_sha=head_sha,
            include_cross_run_failures=include_cross_run_failures,
            include_external_checks=include_external_checks,
            max_actions_runs=max_actions_runs,
            max_external_checks=max_external_checks,
            timeout_seconds=check_settle_timeout_seconds,
            poll_interval_seconds=check_settle_poll_interval_seconds,
        )
        warnings.extend(settlement_warnings)
    elif wait_for_checks_to_settle:
        settlement = {
            **settlement,
            "enabled": True,
            "skipped_reason": "no_cross_run_or_external_checks_enabled",
        }

    if include_cross_run_failures:
        actions_failures, action_warnings = _collect_actions_failures_for_head_sha(
            client,
            owner=owner,
            repo=repo,
            head_sha=head_sha,
            current_run_id=current_run_id,
            current_run_attempt=current_run_attempt,
            max_actions_runs=max_actions_runs,
            max_actions_jobs=max_actions_jobs,
            max_log_lines_per_job=max_log_lines_per_job,
        )
        raw_failures.extend(actions_failures)
        warnings.extend(action_warnings)
    else:
        current_run_failures, action_warnings = _collect_current_run_actions_failures(
            client,
            owner=owner,
            repo=repo,
            head_sha=head_sha,
            current_run_id=current_run_id,
            current_run_attempt=current_run_attempt,
            max_actions_jobs=max_actions_jobs,
            max_log_lines_per_job=max_log_lines_per_job,
        )
        raw_failures.extend(current_run_failures)
        warnings.extend(action_warnings)

    external_failures: list[FailingCheck] = []
    if include_external_checks:
        check_runs, check_warnings = _collect_external_check_runs(
            client,
            owner=owner,
            repo=repo,
            head_sha=head_sha,
            max_external_checks=max_external_checks,
        )
        external_failures.extend(check_runs)
        warnings.extend(check_warnings)

        statuses, status_warnings = _collect_commit_status_failures(
            client,
            owner=owner,
            repo=repo,
            head_sha=head_sha,
            max_external_checks=max_external_checks,
            existing_names={failure.job_name for failure in external_failures},
        )
        external_failures.extend(statuses)
        warnings.extend(status_warnings)

    raw_failures.extend(external_failures)
    deduped_failures = dedupe_failing_checks(raw_failures, max_items=max_failing_checks)
    source_counts = _count_by_source(deduped_failures)

    debug = {
        "raw_failures": [failure.model_dump(mode="json") for failure in raw_failures],
        "deduped_failures": [failure.model_dump(mode="json") for failure in deduped_failures],
        "raw_source_counts": _count_by_source(raw_failures),
        "deduped_source_counts": source_counts,
        "settlement": settlement,
        "warnings": warnings,
    }
    return deduped_failures, debug


def _wait_for_check_settlement(
    client: GitHubApiClient,
    *,
    owner: str,
    repo: str,
    head_sha: str,
    include_cross_run_failures: bool,
    include_external_checks: bool,
    max_actions_runs: int,
    max_external_checks: int,
    timeout_seconds: int,
    poll_interval_seconds: int,
) -> tuple[dict[str, object], list[str]]:
    warnings: list[str] = []
    start = _monotonic()
    last_fingerprint: tuple[str, ...] | None = None
    stable_snapshots = 0
    poll_count = 0
    timeout_seconds = max(timeout_seconds, 0)
    poll_interval_seconds = max(poll_interval_seconds, 0)
    min_wait_seconds = _minimum_check_settle_wait_seconds(
        timeout_seconds=timeout_seconds,
        poll_interval_seconds=poll_interval_seconds,
    )

    while True:
        poll_count += 1
        snapshot, snapshot_warnings = _collect_check_settlement_snapshot(
            client,
            owner=owner,
            repo=repo,
            head_sha=head_sha,
            include_cross_run_failures=include_cross_run_failures,
            include_external_checks=include_external_checks,
            max_actions_runs=max_actions_runs,
            max_external_checks=max_external_checks,
        )
        for warning in snapshot_warnings:
            if warning not in warnings:
                warnings.append(warning)

        fingerprint = tuple(snapshot["fingerprint"])
        if last_fingerprint == fingerprint:
            stable_snapshots += 1
        else:
            stable_snapshots = 0
            last_fingerprint = fingerprint

        elapsed_seconds = max(_monotonic() - start, 0.0)
        settled = (
            snapshot["pending_count"] == 0
            and elapsed_seconds >= min_wait_seconds
            and stable_snapshots >= 1
        )
        timed_out = timeout_seconds > 0 and elapsed_seconds >= timeout_seconds
        if settled or timed_out:
            return (
                {
                    "enabled": True,
                    "settled": settled,
                    "timed_out": timed_out and not settled,
                    "poll_count": poll_count,
                    "elapsed_seconds": round(elapsed_seconds, 3),
                    "min_wait_seconds": min_wait_seconds,
                    "pending_count": snapshot["pending_count"],
                    "snapshot_count": snapshot["snapshot_count"],
                    "actions_run_count": snapshot["actions_run_count"],
                    "external_check_run_count": snapshot["external_check_run_count"],
                    "commit_status_count": snapshot["commit_status_count"],
                    "pending_source_counts": snapshot["pending_source_counts"],
                    "skipped_reason": "",
                    "warnings": warnings,
                },
                warnings,
            )

        if poll_interval_seconds > 0:
            remaining = max(timeout_seconds - elapsed_seconds, 0.0) if timeout_seconds > 0 else 0.0
            sleep_seconds = min(poll_interval_seconds, remaining) if timeout_seconds > 0 else 0.0
            if sleep_seconds > 0:
                _sleep(sleep_seconds)


def _collect_check_settlement_snapshot(
    client: GitHubApiClient,
    *,
    owner: str,
    repo: str,
    head_sha: str,
    include_cross_run_failures: bool,
    include_external_checks: bool,
    max_actions_runs: int,
    max_external_checks: int,
) -> tuple[dict[str, object], list[str]]:
    warnings: list[str] = []
    fingerprint: list[str] = []
    pending_source_counts = {
        "actions_runs": 0,
        "external_check_runs": 0,
        "commit_statuses": 0,
    }
    actions_runs: list[dict[str, object]] = []
    check_runs: list[dict[str, object]] = []
    statuses: list[dict[str, object]] = []

    if include_cross_run_failures:
        actions_runs = _fetch_workflow_run_snapshots_for_head_sha(
            client,
            owner=owner,
            repo=repo,
            head_sha=head_sha,
            max_runs=max_actions_runs,
            warnings=warnings,
        )
        for raw in actions_runs:
            status = str(raw.get("status") or "")
            conclusion = str(raw.get("conclusion") or "")
            fingerprint.append(
                "actions_run::"
                f"{raw.get('id') or ''}::{raw.get('name') or ''}::{status}::{conclusion}"
            )
            if status != "completed":
                pending_source_counts["actions_runs"] += 1

    if include_external_checks:
        check_runs = _fetch_external_check_run_snapshots(
            client,
            owner=owner,
            repo=repo,
            head_sha=head_sha,
            max_external_checks=max_external_checks,
            warnings=warnings,
        )
        for raw in check_runs:
            app = raw.get("app") or {}
            app_name = str(app.get("slug") or app.get("name") or "external")
            name = str(raw.get("name") or "check-run")
            status = str(raw.get("status") or "")
            conclusion = str(raw.get("conclusion") or "")
            fingerprint.append(f"external_check::{app_name}::{name}::{status}::{conclusion}")
            if status != "completed":
                pending_source_counts["external_check_runs"] += 1

        statuses = _fetch_commit_status_snapshots(
            client,
            owner=owner,
            repo=repo,
            head_sha=head_sha,
            max_external_checks=max_external_checks,
            warnings=warnings,
        )
        for raw in statuses:
            context = str(raw.get("context") or "status")
            state = str(raw.get("state") or "")
            fingerprint.append(f"commit_status::{context}::{state}")
            if state not in FAILED_STATUS_STATES | {"success"}:
                pending_source_counts["commit_statuses"] += 1

    fingerprint.sort()
    snapshot_count = len(actions_runs) + len(check_runs) + len(statuses)
    return (
        {
            "fingerprint": fingerprint,
            "pending_count": sum(pending_source_counts.values()),
            "snapshot_count": snapshot_count,
            "actions_run_count": len(actions_runs),
            "external_check_run_count": len(check_runs),
            "commit_status_count": len(statuses),
            "pending_source_counts": {
                key: value for key, value in pending_source_counts.items() if value
            },
        },
        warnings,
    )


def _minimum_check_settle_wait_seconds(
    *,
    timeout_seconds: int,
    poll_interval_seconds: int,
) -> int:
    if timeout_seconds <= 0:
        return 0
    return min(timeout_seconds, max(15, poll_interval_seconds * 3))


def dedupe_failing_checks(
    failures: Iterable[FailingCheck],
    *,
    max_items: int,
) -> list[FailingCheck]:
    grouped: dict[str, list[FailingCheck]] = defaultdict(list)
    for failure in failures:
        grouped[failure.dedupe_key or _fallback_dedupe_key(failure)].append(failure)

    selected = [
        _select_best_failure(group)
        for _, group in sorted(grouped.items(), key=lambda item: item[0])
    ]
    return sorted(selected, key=failing_check_sort_key)[:max_items]


def _collect_current_run_actions_failures(
    client: GitHubApiClient,
    *,
    owner: str,
    repo: str,
    head_sha: str,
    current_run_id: int,
    current_run_attempt: int,
    max_actions_jobs: int,
    max_log_lines_per_job: int,
) -> tuple[list[FailingCheck], list[str]]:
    warnings: list[str] = []
    jobs_payloads = _fetch_run_jobs(
        client,
        owner=owner,
        repo=repo,
        run_id=current_run_id,
        run_attempt=current_run_attempt,
        warnings=warnings,
    )
    run_stub = {
        "id": current_run_id,
        "run_attempt": current_run_attempt,
        "run_number": 0,
        "name": "Current run",
        "display_title": "Current run",
        "conclusion": "failure",
    }
    failures = [
        _normalize_actions_job(
            client,
            owner=owner,
            repo=repo,
            head_sha=head_sha,
            run=raw_job.get("__run__", run_stub),
            raw_job=raw_job,
            current_run_id=current_run_id,
            current_run_attempt=current_run_attempt,
            max_log_lines_per_job=max_log_lines_per_job,
        )
        for raw_job in jobs_payloads
        if str(raw_job.get("conclusion") or "") in FAILED_JOB_CONCLUSIONS
    ]
    return sorted(failures, key=failing_check_sort_key)[:max_actions_jobs], warnings


def _collect_actions_failures_for_head_sha(
    client: GitHubApiClient,
    *,
    owner: str,
    repo: str,
    head_sha: str,
    current_run_id: int,
    current_run_attempt: int,
    max_actions_runs: int,
    max_actions_jobs: int,
    max_log_lines_per_job: int,
) -> tuple[list[FailingCheck], list[str]]:
    warnings: list[str] = []
    runs = _fetch_workflow_runs_for_head_sha(
        client,
        owner=owner,
        repo=repo,
        head_sha=head_sha,
        max_runs=max_actions_runs,
        warnings=warnings,
    )
    job_observations: list[FailingCheck] = []
    run_level_failures: list[FailingCheck] = []

    for run in runs:
        run_id = int(run["id"])
        run_attempt = int(run.get("run_attempt") or 1)
        observed_at = _parse_timestamp(
            str(run.get("updated_at") or run.get("run_started_at") or run.get("created_at") or "")
        )
        jobs = _fetch_run_jobs(
            client,
            owner=owner,
            repo=repo,
            run_id=run_id,
            run_attempt=run_attempt,
            warnings=warnings,
        )

        if jobs:
            for raw_job in jobs:
                job_observations.append(
                    _normalize_actions_job(
                        client,
                        owner=owner,
                        repo=repo,
                        head_sha=head_sha,
                        run=run,
                        raw_job=raw_job,
                        current_run_id=current_run_id,
                        current_run_attempt=current_run_attempt,
                        max_log_lines_per_job=max_log_lines_per_job,
                    )
                )
            continue

        conclusion = str(run.get("conclusion") or "")
        if conclusion in FAILED_CHECK_CONCLUSIONS:
            run_level_failures.append(
                _normalize_actions_run_failure(
                    run,
                    head_sha=head_sha,
                    is_current_run=run_id == current_run_id and run_attempt == current_run_attempt,
                    summary="Workflow run failed, but job details were unavailable.",
                    observed_at=observed_at,
                )
            )

    latest_jobs = _latest_by_key(job_observations)
    selected_jobs = []
    for key, latest in latest_jobs.items():
        if latest.conclusion in FAILED_JOB_CONCLUSIONS:
            candidates = [
                failure
                for failure in job_observations
                if failure.dedupe_key == key and failure.conclusion in FAILED_JOB_CONCLUSIONS
            ]
            selected_jobs.append(_select_best_failure(candidates))

    selected_jobs = sorted(selected_jobs, key=failing_check_sort_key)[:max_actions_jobs]
    selected_run_ids = {failure.run_id for failure in selected_jobs if failure.run_id is not None}
    selected_runs = [
        failure for failure in run_level_failures if failure.run_id not in selected_run_ids
    ]
    return selected_jobs + selected_runs, warnings


def _collect_external_check_runs(
    client: GitHubApiClient,
    *,
    owner: str,
    repo: str,
    head_sha: str,
    max_external_checks: int,
) -> tuple[list[FailingCheck], list[str]]:
    warnings: list[str] = []
    check_runs: list[FailingCheck] = []
    page = 1
    while len(check_runs) < max_external_checks:
        payload = _safe_request_json(
            client,
            "GET",
            f"/repos/{owner}/{repo}/commits/{head_sha}/check-runs",
            warnings=warnings,
            warning_prefix="Unable to fetch check runs",
            params={"per_page": 100, "page": page},
        )
        if not payload:
            break
        raw_page = payload.get("check_runs", [])
        if not raw_page:
            break
        check_runs.extend(
            _normalize_external_check_run(raw, head_sha=head_sha)
            for raw in raw_page
            if _is_relevant_external_check_run(raw)
        )
        if len(raw_page) < 100:
            break
        page += 1
    latest = _latest_by_key(check_runs)
    failures = [
        failure for failure in latest.values() if failure.conclusion in FAILED_CHECK_CONCLUSIONS
    ]
    return sorted(failures, key=failing_check_sort_key)[:max_external_checks], warnings


def _collect_commit_status_failures(
    client: GitHubApiClient,
    *,
    owner: str,
    repo: str,
    head_sha: str,
    max_external_checks: int,
    existing_names: set[str],
) -> tuple[list[FailingCheck], list[str]]:
    warnings: list[str] = []
    payload = _safe_request_json(
        client,
        "GET",
        f"/repos/{owner}/{repo}/commits/{head_sha}/status",
        warnings=warnings,
        warning_prefix="Unable to fetch commit statuses",
    )
    if not payload:
        return [], warnings

    statuses = [
        _normalize_commit_status(raw, head_sha=head_sha)
        for raw in payload.get("statuses", [])
        if str(raw.get("context") or "") not in existing_names
    ]
    latest = _latest_by_key(statuses)
    failures = [failure for failure in latest.values() if failure.status in FAILED_STATUS_STATES]
    return sorted(failures, key=failing_check_sort_key)[:max_external_checks], warnings


def _fetch_workflow_run_snapshots_for_head_sha(
    client: GitHubApiClient,
    *,
    owner: str,
    repo: str,
    head_sha: str,
    max_runs: int,
    warnings: list[str],
) -> list[dict[str, object]]:
    collected: list[dict[str, object]] = []
    page = 1
    while len(collected) < max_runs:
        payload = _safe_request_json(
            client,
            "GET",
            f"/repos/{owner}/{repo}/actions/runs",
            warnings=warnings,
            warning_prefix=f"Unable to fetch workflow runs for head SHA {head_sha}",
            params={"head_sha": head_sha, "per_page": 100, "page": page},
        )
        if not payload:
            break
        runs = payload.get("workflow_runs", [])
        if not runs:
            break
        collected.extend(runs)
        if len(runs) < 100:
            break
        page += 1
    return sorted(
        collected,
        key=lambda run: (
            str(run.get("updated_at") or run.get("created_at") or ""),
            int(run.get("id") or 0),
        ),
        reverse=True,
    )[:max_runs]


def _fetch_external_check_run_snapshots(
    client: GitHubApiClient,
    *,
    owner: str,
    repo: str,
    head_sha: str,
    max_external_checks: int,
    warnings: list[str],
) -> list[dict[str, object]]:
    collected: list[dict[str, object]] = []
    page = 1
    while len(collected) < max_external_checks:
        payload = _safe_request_json(
            client,
            "GET",
            f"/repos/{owner}/{repo}/commits/{head_sha}/check-runs",
            warnings=warnings,
            warning_prefix="Unable to poll check runs",
            params={"per_page": 100, "page": page},
        )
        if not payload:
            break
        raw_page = payload.get("check_runs", [])
        if not raw_page:
            break
        collected.extend(raw for raw in raw_page if _is_relevant_settlement_check_run(raw))
        if len(raw_page) < 100:
            break
        page += 1
    return sorted(
        collected,
        key=lambda raw: (
            str(raw.get("completed_at") or raw.get("started_at") or ""),
            str(raw.get("name") or ""),
            int(raw.get("id") or 0),
        ),
        reverse=True,
    )[:max_external_checks]


def _fetch_commit_status_snapshots(
    client: GitHubApiClient,
    *,
    owner: str,
    repo: str,
    head_sha: str,
    max_external_checks: int,
    warnings: list[str],
) -> list[dict[str, object]]:
    payload = _safe_request_json(
        client,
        "GET",
        f"/repos/{owner}/{repo}/commits/{head_sha}/status",
        warnings=warnings,
        warning_prefix="Unable to poll commit statuses",
    )
    if not payload:
        return []
    return list(payload.get("statuses", []))[:max_external_checks]


def _fetch_workflow_runs_for_head_sha(
    client: GitHubApiClient,
    *,
    owner: str,
    repo: str,
    head_sha: str,
    max_runs: int,
    warnings: list[str],
) -> list[dict[str, object]]:
    return [
        run
        for run in _fetch_workflow_run_snapshots_for_head_sha(
            client,
            owner=owner,
            repo=repo,
            head_sha=head_sha,
            max_runs=max_runs,
            warnings=warnings,
        )
        if str(run.get("status") or "completed") == "completed"
    ][:max_runs]


def _normalize_actions_job(
    client: GitHubApiClient,
    *,
    owner: str,
    repo: str,
    head_sha: str,
    run: dict[str, object],
    raw_job: dict[str, object],
    current_run_id: int,
    current_run_attempt: int,
    max_log_lines_per_job: int,
) -> FailingCheck:
    run_id = int(run["id"])
    run_attempt = int(run.get("run_attempt") or 1)
    job_id = int(raw_job["id"])
    log_bytes = b""
    excerpt_lines: list[str] = []
    logs_available = False
    if str(raw_job.get("conclusion") or "") in FAILED_JOB_CONCLUSIONS:
        try:
            log_bytes = client.request_bytes(
                "GET",
                f"/repos/{owner}/{repo}/actions/jobs/{job_id}/logs",
            )
            excerpt_lines = trim_log_excerpt(
                extract_log_text(log_bytes),
                failed_steps=_extract_failed_steps(raw_job),
                max_lines=max_log_lines_per_job,
            )
            logs_available = bool(excerpt_lines)
        except GitHubApiError:
            excerpt_lines = []

    job_name, matrix_label = split_job_display_name(str(raw_job.get("name") or f"job-{job_id}"))
    workflow_name = str(run.get("name") or raw_job.get("workflow_name") or "Workflow")
    return FailingCheck(
        source_type="actions_job",
        job_id=job_id,
        workflow_name=workflow_name,
        job_name=job_name,
        matrix_label=matrix_label,
        summary=str(run.get("display_title") or raw_job.get("name") or ""),
        conclusion=str(raw_job.get("conclusion") or ""),
        url=str(raw_job.get("html_url") or run.get("html_url") or ""),
        failed_steps=_extract_failed_steps(raw_job),
        excerpt_lines=excerpt_lines,
        head_sha=head_sha,
        is_current_run=run_id == current_run_id and run_attempt == current_run_attempt,
        logs_available=logs_available,
        details_available=logs_available or bool(raw_job.get("html_url")),
        dedupe_key=f"actions_job::{workflow_name}::{job_name}::{matrix_label or ''}",
        observed_at=_parse_timestamp(
            str(raw_job.get("completed_at") or run.get("updated_at") or "")
        ),
        run_id=run_id,
        run_attempt=run_attempt,
        run_number=int(run.get("run_number") or 0),
    )


def _normalize_actions_run_failure(
    run: dict[str, object],
    *,
    head_sha: str,
    is_current_run: bool,
    summary: str,
    observed_at: datetime | None,
) -> FailingCheck:
    workflow_name = str(run.get("name") or "Workflow")
    return FailingCheck(
        source_type="actions_workflow_run",
        workflow_name=workflow_name,
        job_name=str(run.get("display_title") or workflow_name),
        summary=summary,
        conclusion=str(run.get("conclusion") or ""),
        url=str(run.get("html_url") or ""),
        head_sha=head_sha,
        is_current_run=is_current_run,
        logs_available=False,
        details_available=bool(run.get("html_url")),
        dedupe_key=f"actions_run::{workflow_name}",
        observed_at=observed_at,
        run_id=int(run["id"]),
        run_attempt=int(run.get("run_attempt") or 1),
        run_number=int(run.get("run_number") or 0),
    )


def _normalize_external_check_run(
    raw: dict[str, object],
    *,
    head_sha: str,
) -> FailingCheck:
    app = raw.get("app") or {}
    output = raw.get("output") or {}
    app_name = str(app.get("slug") or app.get("name") or "external")
    name = str(raw.get("name") or "check-run")
    summary = "\n".join(
        part
        for part in (
            str(output.get("title") or "").strip(),
            str(output.get("summary") or "").strip(),
        )
        if part
    )
    excerpt_lines = []
    text = str(output.get("text") or "").strip()
    if text:
        excerpt_lines = [line for line in text.splitlines() if line.strip()][:8]
    return FailingCheck(
        source_type="external_check_run",
        workflow_name=app_name,
        job_name=name,
        app_name=app_name,
        summary=summary or None,
        status=str(raw.get("status") or ""),
        conclusion=str(raw.get("conclusion") or ""),
        url=str(raw.get("details_url") or raw.get("html_url") or ""),
        excerpt_lines=excerpt_lines,
        head_sha=head_sha,
        details_available=bool(summary or excerpt_lines or raw.get("details_url")),
        dedupe_key=f"external_check::{app_name}::{name}",
        observed_at=_parse_timestamp(str(raw.get("completed_at") or raw.get("started_at") or "")),
    )


def _normalize_commit_status(
    raw: dict[str, object],
    *,
    head_sha: str,
) -> FailingCheck:
    context = str(raw.get("context") or "status")
    description = str(raw.get("description") or "").strip()
    return FailingCheck(
        source_type="commit_status",
        workflow_name="Commit status",
        job_name=context,
        context_name=context,
        summary=description or None,
        status=str(raw.get("state") or ""),
        url=str(raw.get("target_url") or ""),
        head_sha=head_sha,
        details_available=bool(description or raw.get("target_url")),
        dedupe_key=f"commit_status::{context}",
        observed_at=_parse_timestamp(str(raw.get("updated_at") or raw.get("created_at") or "")),
    )


def _latest_by_key(failures: Iterable[FailingCheck]) -> dict[str, FailingCheck]:
    latest: dict[str, FailingCheck] = {}
    for failure in failures:
        key = failure.dedupe_key or _fallback_dedupe_key(failure)
        previous = latest.get(key)
        if previous is None or _observation_sort_key(failure) > _observation_sort_key(previous):
            latest[key] = failure
    return latest


def _select_best_failure(group: list[FailingCheck]) -> FailingCheck:
    return max(group, key=_selection_score)


def _observation_sort_key(failure: FailingCheck) -> tuple[int, int, str]:
    return (
        int(failure.observed_at.timestamp()) if failure.observed_at else 0,
        failure.run_attempt or 0,
        failure.url,
    )


def _selection_score(failure: FailingCheck) -> tuple[int, int, int, str]:
    detail_score = 0
    if failure.logs_available:
        detail_score += 4
    if failure.excerpt_lines:
        detail_score += 2
    if failure.failed_steps:
        detail_score += 1
    if failure.summary:
        detail_score += 1
    return (
        1 if failure.is_current_run else 0,
        detail_score,
        int(failure.observed_at.timestamp()) if failure.observed_at else 0,
        failure.url,
    )


def _count_by_source(failures: Iterable[FailingCheck]) -> dict[str, int]:
    counts: dict[str, int] = defaultdict(int)
    for failure in failures:
        counts[failure.source_type] += 1
    return dict(sorted(counts.items()))


def _extract_failed_steps(raw_job: dict[str, object]) -> list[str]:
    return [
        str(step.get("name") or "")
        for step in raw_job.get("steps") or []
        if step.get("conclusion") in FAILED_STEP_CONCLUSIONS
    ]


def _fetch_run_jobs(
    client: GitHubApiClient,
    *,
    owner: str,
    repo: str,
    run_id: int,
    run_attempt: int,
    warnings: list[str],
) -> list[dict[str, object]]:
    jobs: list[dict[str, object]] = []
    page = 1
    while True:
        payload = _safe_request_json(
            client,
            "GET",
            f"/repos/{owner}/{repo}/actions/runs/{run_id}/attempts/{run_attempt}/jobs",
            warnings=warnings,
            warning_prefix=f"Unable to fetch jobs for workflow run {run_id}",
            params={"per_page": 100, "page": page},
        )
        if not payload:
            break
        page_jobs = list(payload.get("jobs", []))
        if not page_jobs:
            break
        for raw_job in page_jobs:
            raw_job["__run__"] = {
                "id": run_id,
                "run_attempt": run_attempt,
                "run_number": 0,
                "name": raw_job.get("workflow_name") or "Workflow",
                "display_title": raw_job.get("name") or raw_job.get("workflow_name") or "Job",
                "conclusion": raw_job.get("conclusion") or "",
                "html_url": raw_job.get("html_url") or "",
            }
        jobs.extend(page_jobs)
        if len(page_jobs) < 100:
            break
        page += 1
    return jobs


def _safe_request_json(
    client: GitHubApiClient,
    method: str,
    path: str,
    *,
    warnings: list[str],
    warning_prefix: str,
    params: dict[str, object] | None = None,
) -> dict[str, object] | None:
    try:
        return client.request_json(method, path, params=params)
    except GitHubApiError as error:
        warnings.append(f"{warning_prefix}: {error}")
        return None


def _parse_timestamp(value: str) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _fallback_dedupe_key(failure: FailingCheck) -> str:
    return (
        f"{failure.source_type}::{failure.workflow_name}::{failure.job_name}::"
        f"{failure.matrix_label or ''}::{failure.app_name or ''}"
    )


def _is_relevant_external_check_run(raw: dict[str, object]) -> bool:
    app = raw.get("app") or {}
    app_name = str(app.get("slug") or app.get("name") or "")
    status = str(raw.get("status") or "")
    conclusion = str(raw.get("conclusion") or "")
    if app_name in ACTIONS_APP_NAMES:
        return False
    if status != "completed":
        return False
    return conclusion in FAILED_CHECK_CONCLUSIONS


def _is_relevant_settlement_check_run(raw: dict[str, object]) -> bool:
    app = raw.get("app") or {}
    app_name = str(app.get("slug") or app.get("name") or "")
    if app_name in ACTIONS_APP_NAMES:
        return False
    return True
