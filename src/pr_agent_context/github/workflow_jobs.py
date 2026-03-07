from __future__ import annotations

import re
from collections.abc import Callable, Iterable

from pr_agent_context.constants import ERROR_MARKERS, FAILED_JOB_CONCLUSIONS
from pr_agent_context.domain.models import WorkflowFailure
from pr_agent_context.github.api import GitHubApiClient

FAILED_STEP_CONCLUSIONS = {"failure", "timed_out", "startup_failure", "cancelled"}
_MATRIX_SUFFIX_RE = re.compile(r"^(?P<job>.+?) \((?P<matrix>.+)\)$")


def collect_failed_jobs(
    client: GitHubApiClient,
    *,
    owner: str,
    repo: str,
    run_id: int,
    run_attempt: int,
    max_failed_jobs: int,
    max_log_lines_per_job: int,
) -> list[WorkflowFailure]:
    jobs: list[dict[str, object]] = []
    page = 1
    while True:
        payload = client.request_json(
            "GET",
            f"/repos/{owner}/{repo}/actions/runs/{run_id}/attempts/{run_attempt}/jobs",
            params={"per_page": 100, "page": page},
        )
        jobs.extend(payload.get("jobs", []))
        if len(payload.get("jobs", [])) < 100:
            break
        page += 1

    return parse_failed_jobs(
        jobs,
        log_fetcher=lambda job_id: client.request_text(
            "GET",
            f"/repos/{owner}/{repo}/actions/jobs/{job_id}/logs",
        ),
        max_failed_jobs=max_failed_jobs,
        max_log_lines_per_job=max_log_lines_per_job,
    )


def parse_failed_jobs(
    jobs: Iterable[dict[str, object]],
    *,
    log_fetcher: Callable[[int], str],
    max_failed_jobs: int,
    max_log_lines_per_job: int,
) -> list[WorkflowFailure]:
    failures: list[WorkflowFailure] = []
    for raw_job in jobs:
        conclusion = raw_job.get("conclusion")
        if conclusion not in FAILED_JOB_CONCLUSIONS:
            continue
        job_id = int(raw_job["id"])
        failed_steps = [
            step.get("name", "")
            for step in raw_job.get("steps") or []
            if step.get("conclusion") in FAILED_STEP_CONCLUSIONS
        ]
        log_text = log_fetcher(job_id)
        job_name, matrix_label = split_job_display_name(str(raw_job.get("name", f"job-{job_id}")))
        failures.append(
            WorkflowFailure(
                job_id=job_id,
                workflow_name=str(raw_job.get("workflow_name") or "Workflow"),
                job_name=job_name,
                matrix_label=matrix_label,
                conclusion=str(conclusion),
                url=str(raw_job.get("html_url") or ""),
                failed_steps=failed_steps,
                excerpt_lines=trim_log_excerpt(
                    log_text,
                    failed_steps=failed_steps,
                    max_lines=max_log_lines_per_job,
                ),
            )
        )
    return sorted(
        failures,
        key=lambda failure: (
            failure.workflow_name,
            failure.job_name,
            failure.matrix_label or "",
            failure.job_id,
        ),
    )[:max_failed_jobs]


def split_job_display_name(name: str) -> tuple[str, str | None]:
    match = _MATRIX_SUFFIX_RE.match(name.strip())
    if not match:
        return name.strip(), None
    return match.group("job"), match.group("matrix")


def trim_log_excerpt(log_text: str, *, failed_steps: list[str], max_lines: int) -> list[str]:
    lines = log_text.splitlines()
    if not lines:
        return []

    selected_indexes: set[int] = set()
    weighted_anchors: list[tuple[int, int]] = []
    for index, line in enumerate(lines):
        score = 0
        if any(marker in line for marker in ERROR_MARKERS):
            score = 3
        elif any(step and step in line for step in failed_steps):
            score = 2
        if score:
            weighted_anchors.append((index, score))

    for index, score in sorted(weighted_anchors, key=lambda item: (-item[1], item[0])):
        radius_after = 10 if score == 2 else 4
        start = max(index - 2, 0)
        stop = min(index + radius_after + 1, len(lines))
        for line_index in range(start, stop):
            if len(selected_indexes) >= max_lines and line_index not in selected_indexes:
                break
            selected_indexes.add(line_index)
        if len(selected_indexes) >= max_lines:
            break

    if not selected_indexes:
        return lines[-max_lines:]
    excerpt = [lines[index] for index in sorted(selected_indexes)]
    return excerpt[:max_lines]
