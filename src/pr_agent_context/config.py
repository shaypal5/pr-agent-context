from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from pr_agent_context.constants import (
    DEFAULT_COVERAGE_ARTIFACT_PREFIX,
    DEFAULT_MAX_FAILED_JOBS,
    DEFAULT_MAX_LOG_LINES_PER_JOB,
    DEFAULT_MAX_REVIEW_THREADS,
    DEFAULT_TARGET_PATCH_COVERAGE,
)


def _parse_bool(value: str | bool | None, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return value.strip().lower() in {"1", "true", "yes", "on"}


class PullRequestRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    owner: str
    repo: str
    number: int
    base_sha: str
    head_sha: str


class RunConfig(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    github_api_url: str = "https://api.github.com"
    github_token: str
    pull_request: PullRequestRef
    run_id: int
    run_attempt: int
    workspace: Path
    prompt_preamble: str = ""
    max_review_threads: int = DEFAULT_MAX_REVIEW_THREADS
    max_failed_jobs: int = DEFAULT_MAX_FAILED_JOBS
    max_log_lines_per_job: int = DEFAULT_MAX_LOG_LINES_PER_JOB
    target_patch_coverage: float = DEFAULT_TARGET_PATCH_COVERAGE
    include_patch_coverage: bool = True
    coverage_artifact_prefix: str = DEFAULT_COVERAGE_ARTIFACT_PREFIX
    force_patch_coverage_section: bool = False
    delete_comment_when_empty: bool = True
    skip_comment_on_readonly_token: bool = True
    coverage_artifacts_dir: Path | None = Field(default=None)
    github_output_path: Path | None = Field(default=None)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> RunConfig:
        env_map = dict(os.environ if env is None else env)
        repository = env_map["GITHUB_REPOSITORY"]
        owner, repo = repository.split("/", maxsplit=1)
        event = _load_event_payload(env_map["GITHUB_EVENT_PATH"])
        pull_request_number = _extract_pull_request_number(event)
        base_sha, head_sha = _extract_pull_request_shas(event)

        return cls(
            github_api_url=env_map.get("GITHUB_API_URL", "https://api.github.com"),
            github_token=env_map["GITHUB_TOKEN"],
            pull_request=PullRequestRef(
                owner=owner,
                repo=repo,
                number=pull_request_number,
                base_sha=base_sha,
                head_sha=head_sha,
            ),
            run_id=int(env_map["GITHUB_RUN_ID"]),
            run_attempt=int(env_map.get("GITHUB_RUN_ATTEMPT", "1")),
            workspace=Path(env_map.get("PR_AGENT_CONTEXT_WORKSPACE", os.getcwd())),
            prompt_preamble=env_map.get("PR_AGENT_CONTEXT_PROMPT_PREAMBLE", "").strip(),
            max_review_threads=int(
                env_map.get(
                    "PR_AGENT_CONTEXT_MAX_REVIEW_THREADS",
                    str(DEFAULT_MAX_REVIEW_THREADS),
                )
            ),
            max_failed_jobs=int(
                env_map.get(
                    "PR_AGENT_CONTEXT_MAX_FAILED_JOBS",
                    str(DEFAULT_MAX_FAILED_JOBS),
                )
            ),
            max_log_lines_per_job=int(
                env_map.get(
                    "PR_AGENT_CONTEXT_MAX_LOG_LINES_PER_JOB",
                    str(DEFAULT_MAX_LOG_LINES_PER_JOB),
                )
            ),
            target_patch_coverage=float(
                env_map.get(
                    "PR_AGENT_CONTEXT_TARGET_PATCH_COVERAGE",
                    str(DEFAULT_TARGET_PATCH_COVERAGE),
                )
            ),
            include_patch_coverage=_parse_bool(
                env_map.get("PR_AGENT_CONTEXT_INCLUDE_PATCH_COVERAGE"),
                default=True,
            ),
            coverage_artifact_prefix=env_map.get(
                "PR_AGENT_CONTEXT_COVERAGE_ARTIFACT_PREFIX",
                DEFAULT_COVERAGE_ARTIFACT_PREFIX,
            ).strip()
            or DEFAULT_COVERAGE_ARTIFACT_PREFIX,
            force_patch_coverage_section=_parse_bool(
                env_map.get("PR_AGENT_CONTEXT_FORCE_PATCH_COVERAGE_SECTION"),
                default=False,
            ),
            delete_comment_when_empty=_parse_bool(
                env_map.get("PR_AGENT_CONTEXT_DELETE_COMMENT_WHEN_EMPTY"),
                default=True,
            ),
            skip_comment_on_readonly_token=_parse_bool(
                env_map.get("PR_AGENT_CONTEXT_SKIP_COMMENT_ON_READONLY_TOKEN"),
                default=True,
            ),
            coverage_artifacts_dir=(
                Path(env_map["PR_AGENT_CONTEXT_COVERAGE_ARTIFACTS_DIR"])
                if env_map.get("PR_AGENT_CONTEXT_COVERAGE_ARTIFACTS_DIR")
                else None
            ),
            github_output_path=(
                Path(env_map["GITHUB_OUTPUT"]) if env_map.get("GITHUB_OUTPUT") else None
            ),
        )


def _load_event_payload(path: str) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _extract_pull_request_number(event: Mapping[str, Any]) -> int:
    if "pull_request" in event and isinstance(event["pull_request"], Mapping):
        pull_request = event["pull_request"]
        if "number" in pull_request:
            return int(pull_request["number"])
    if "number" in event:
        return int(event["number"])
    raise ValueError("Unable to determine pull request number from event payload.")


def _extract_pull_request_shas(event: Mapping[str, Any]) -> tuple[str, str]:
    pull_request = event.get("pull_request")
    if not isinstance(pull_request, Mapping):
        raise ValueError("Unable to determine pull request SHAs from event payload.")
    base = pull_request.get("base")
    head = pull_request.get("head")
    if not isinstance(base, Mapping) or not isinstance(head, Mapping):
        raise ValueError("Unable to determine pull request SHAs from event payload.")
    base_sha = str(base.get("sha") or "").strip()
    head_sha = str(head.get("sha") or "").strip()
    if not base_sha or not head_sha:
        raise ValueError("Pull request event is missing base/head SHAs.")
    return base_sha, head_sha
