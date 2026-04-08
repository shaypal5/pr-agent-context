from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from io import BytesIO
from zipfile import BadZipFile, ZipFile

from pr_agent_context.constants import ERROR_MARKERS, FAILED_JOB_CONCLUSIONS
from pr_agent_context.domain.models import FailingCheck
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
    max_actions_jobs: int,
    max_log_lines_per_job: int,
) -> list[FailingCheck]:
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
        log_fetcher=lambda job_id: extract_log_text(
            client.request_bytes(
                "GET",
                f"/repos/{owner}/{repo}/actions/jobs/{job_id}/logs",
            )
        ),
        max_actions_jobs=max_actions_jobs,
        max_log_lines_per_job=max_log_lines_per_job,
    )


def parse_failed_jobs(
    jobs: Iterable[dict[str, object]],
    *,
    log_fetcher: Callable[[int], str],
    max_actions_jobs: int,
    max_log_lines_per_job: int,
) -> list[FailingCheck]:
    failures: list[FailingCheck] = []
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
            FailingCheck(
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
    )[:max_actions_jobs]


def split_job_display_name(name: str) -> tuple[str, str | None]:
    match = _MATRIX_SUFFIX_RE.match(name.strip())
    if not match:
        return name.strip(), None
    return match.group("job"), match.group("matrix")


def trim_log_excerpt(log_text: str, *, failed_steps: list[str], max_lines: int) -> list[str]:
    return trim_log_excerpt_lines(
        log_text.splitlines(),
        failed_steps=failed_steps,
        max_lines=max_lines,
    )


def trim_log_excerpt_lines(
    lines: list[str],
    *,
    failed_steps: list[str],
    max_lines: int,
) -> list[str]:
    if not lines:
        return []
    if max_lines <= 0:
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


def extract_failed_step_output(
    log_text: str,
    *,
    failed_steps: list[str],
    max_lines: int,
) -> tuple[str | None, list[str]]:
    return extract_failed_step_output_lines(
        log_text.splitlines(),
        failed_steps=failed_steps,
        max_lines=max_lines,
    )


def extract_failed_step_output_lines(
    lines: list[str],
    *,
    failed_steps: list[str],
    max_lines: int,
) -> tuple[str | None, list[str]]:
    if max_lines <= 0 or not failed_steps or not lines:
        return None, []

    grouped_steps = _group_step_blocks(lines)
    if not grouped_steps:
        return None, []

    normalized_lookup = {}
    for step_name, step_lines in grouped_steps:
        normalized_lookup.setdefault(_normalize_step_name(step_name), []).append(
            (step_name, step_lines)
        )

    for failed_step in failed_steps:
        matches = normalized_lookup.get(_normalize_step_name(failed_step), [])
        if len(matches) != 1:
            continue
        step_name, step_lines = matches[0]
        trimmed_step_lines = _trim_trailing_blank_lines(step_lines)
        return step_name, trimmed_step_lines[:max_lines]

    return None, []


def _group_step_blocks(lines: list[str]) -> list[tuple[str, list[str]]]:
    blocks: list[tuple[str, list[str]]] = []
    current_step: str | None = None
    current_lines: list[str] = []

    for line in lines:
        step_name = _extract_grouped_step_name(line)
        if step_name is not None:
            if current_step is not None:
                blocks.append((current_step, current_lines))
            current_step = step_name
            current_lines = [line]
            continue

        if current_step is None:
            continue

        if line.strip() == "##[endgroup]":
            current_lines.append(line)
            blocks.append((current_step, current_lines))
            current_step = None
            current_lines = []
            continue

        current_lines.append(line)

    if current_step is not None:
        blocks.append((current_step, current_lines))

    return blocks


def _extract_grouped_step_name(line: str) -> str | None:
    marker = "##[group]Run "
    if marker not in line:
        return None
    return line.split(marker, maxsplit=1)[1].strip() or None


def _normalize_step_name(name: str) -> str:
    normalized = " ".join(name.strip().split())
    if normalized.casefold().startswith("run "):
        normalized = normalized[4:]
    return normalized.casefold()


def _trim_trailing_blank_lines(lines: list[str]) -> list[str]:
    trimmed = list(lines)
    while trimmed and not trimmed[-1].strip():
        trimmed.pop()
    return trimmed


def extract_log_text(log_payload: bytes) -> str:
    try:
        with ZipFile(BytesIO(log_payload)) as archive:
            entries = sorted(
                (info for info in archive.infolist() if not info.is_dir()),
                key=lambda info: info.filename,
            )
            extracted_parts: list[str] = []
            for entry in entries:
                with archive.open(entry) as handle:
                    extracted_parts.append(handle.read().decode("utf-8", errors="replace"))
            if extracted_parts:
                return "\n".join(part.rstrip("\n") for part in extracted_parts if part)
    except BadZipFile:
        pass
    return log_payload.decode("utf-8", errors="replace")
