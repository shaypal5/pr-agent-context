from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime

from pr_agent_context.constants import (
    ACTIONS_APP_NAMES,
    FAILED_CHECK_CONCLUSIONS,
    FAILED_JOB_CONCLUSIONS,
    FAILED_STATUS_STATES,
)
from pr_agent_context.domain.models import WorkflowFailure, workflow_failure_sort_key
from pr_agent_context.github.api import GitHubApiClient, GitHubApiError
from pr_agent_context.github.workflow_jobs import (
    FAILED_STEP_CONCLUSIONS,
    extract_log_text,
    split_job_display_name,
    trim_log_excerpt,
)


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
    max_failed_runs: int,
    max_failed_jobs: int,
    max_external_checks: int,
    max_failing_items: int,
    max_log_lines_per_job: int,
) -> tuple[list[WorkflowFailure], dict[str, object]]:
    warnings: list[str] = []
    raw_failures: list[WorkflowFailure] = []

    if include_cross_run_failures:
        actions_failures, action_warnings = _collect_actions_failures_for_head_sha(
            client,
            owner=owner,
            repo=repo,
            head_sha=head_sha,
            current_run_id=current_run_id,
            current_run_attempt=current_run_attempt,
            max_failed_runs=max_failed_runs,
            max_failed_jobs=max_failed_jobs,
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
            max_failed_jobs=max_failed_jobs,
            max_log_lines_per_job=max_log_lines_per_job,
        )
        raw_failures.extend(current_run_failures)
        warnings.extend(action_warnings)

    external_failures: list[WorkflowFailure] = []
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
    deduped_failures = dedupe_failing_checks(raw_failures, max_items=max_failing_items)
    source_counts = _count_by_source(deduped_failures)

    debug = {
        "raw_failures": [failure.model_dump(mode="json") for failure in raw_failures],
        "deduped_failures": [failure.model_dump(mode="json") for failure in deduped_failures],
        "raw_source_counts": _count_by_source(raw_failures),
        "deduped_source_counts": source_counts,
        "warnings": warnings,
    }
    return deduped_failures, debug


def dedupe_failing_checks(
    failures: Iterable[WorkflowFailure],
    *,
    max_items: int,
) -> list[WorkflowFailure]:
    grouped: dict[str, list[WorkflowFailure]] = defaultdict(list)
    for failure in failures:
        grouped[failure.dedupe_key or _fallback_dedupe_key(failure)].append(failure)

    selected = [
        _select_best_failure(group)
        for _, group in sorted(grouped.items(), key=lambda item: item[0])
    ]
    return sorted(selected, key=workflow_failure_sort_key)[:max_items]


def _collect_current_run_actions_failures(
    client: GitHubApiClient,
    *,
    owner: str,
    repo: str,
    head_sha: str,
    current_run_id: int,
    current_run_attempt: int,
    max_failed_jobs: int,
    max_log_lines_per_job: int,
) -> tuple[list[WorkflowFailure], list[str]]:
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
    return sorted(failures, key=workflow_failure_sort_key)[:max_failed_jobs], warnings


def _collect_actions_failures_for_head_sha(
    client: GitHubApiClient,
    *,
    owner: str,
    repo: str,
    head_sha: str,
    current_run_id: int,
    current_run_attempt: int,
    max_failed_runs: int,
    max_failed_jobs: int,
    max_log_lines_per_job: int,
) -> tuple[list[WorkflowFailure], list[str]]:
    warnings: list[str] = []
    runs = _fetch_workflow_runs_for_head_sha(
        client,
        owner=owner,
        repo=repo,
        head_sha=head_sha,
        max_runs=max_failed_runs,
    )
    job_observations: list[WorkflowFailure] = []
    run_level_failures: list[WorkflowFailure] = []

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
        if conclusion in FAILED_JOB_CONCLUSIONS:
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
            candidates = [failure for failure in job_observations if failure.dedupe_key == key]
            selected_jobs.append(_select_best_failure(candidates))

    selected_jobs = sorted(selected_jobs, key=workflow_failure_sort_key)[:max_failed_jobs]
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
) -> tuple[list[WorkflowFailure], list[str]]:
    warnings: list[str] = []
    check_runs: list[WorkflowFailure] = []
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
    return sorted(failures, key=workflow_failure_sort_key)[:max_external_checks], warnings


def _collect_commit_status_failures(
    client: GitHubApiClient,
    *,
    owner: str,
    repo: str,
    head_sha: str,
    max_external_checks: int,
    existing_names: set[str],
) -> tuple[list[WorkflowFailure], list[str]]:
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
    return sorted(failures, key=workflow_failure_sort_key)[:max_external_checks], warnings


def _fetch_workflow_runs_for_head_sha(
    client: GitHubApiClient,
    *,
    owner: str,
    repo: str,
    head_sha: str,
    max_runs: int,
) -> list[dict[str, object]]:
    collected: list[dict[str, object]] = []
    page = 1
    while len(collected) < max_runs:
        payload = client.request_json(
            "GET",
            f"/repos/{owner}/{repo}/actions/runs",
            params={
                "head_sha": head_sha,
                "status": "completed",
                "per_page": 100,
                "page": page,
            },
        )
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
) -> WorkflowFailure:
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
    return WorkflowFailure(
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
) -> WorkflowFailure:
    workflow_name = str(run.get("name") or "Workflow")
    return WorkflowFailure(
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
) -> WorkflowFailure:
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
    return WorkflowFailure(
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
) -> WorkflowFailure:
    context = str(raw.get("context") or "status")
    description = str(raw.get("description") or "").strip()
    return WorkflowFailure(
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


def _latest_by_key(failures: Iterable[WorkflowFailure]) -> dict[str, WorkflowFailure]:
    latest: dict[str, WorkflowFailure] = {}
    for failure in failures:
        key = failure.dedupe_key or _fallback_dedupe_key(failure)
        previous = latest.get(key)
        if previous is None or _observation_sort_key(failure) > _observation_sort_key(previous):
            latest[key] = failure
    return latest


def _select_best_failure(group: list[WorkflowFailure]) -> WorkflowFailure:
    return max(group, key=_selection_score)


def _observation_sort_key(failure: WorkflowFailure) -> tuple[int, int, str]:
    return (
        int(failure.observed_at.timestamp()) if failure.observed_at else 0,
        failure.run_attempt or 0,
        failure.url,
    )


def _selection_score(failure: WorkflowFailure) -> tuple[int, int, int, str]:
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


def _count_by_source(failures: Iterable[WorkflowFailure]) -> dict[str, int]:
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


def _fallback_dedupe_key(failure: WorkflowFailure) -> str:
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
