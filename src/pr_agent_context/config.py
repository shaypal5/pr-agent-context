from __future__ import annotations

import json
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from pr_agent_context.constants import (
    DEFAULT_MAX_FAILED_JOBS,
    DEFAULT_MAX_LOG_LINES_PER_JOB,
    DEFAULT_MAX_REVIEW_THREADS,
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
    delete_comment_when_empty: bool = True
    skip_comment_on_readonly_token: bool = True
    github_output_path: Path | None = Field(default=None)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> RunConfig:
        env_map = dict(os.environ if env is None else env)
        repository = env_map["GITHUB_REPOSITORY"]
        owner, repo = repository.split("/", maxsplit=1)
        event = _load_event_payload(env_map["GITHUB_EVENT_PATH"])
        pull_request_number = _extract_pull_request_number(event)

        return cls(
            github_api_url=env_map.get("GITHUB_API_URL", "https://api.github.com"),
            github_token=env_map["GITHUB_TOKEN"],
            pull_request=PullRequestRef(owner=owner, repo=repo, number=pull_request_number),
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
            delete_comment_when_empty=_parse_bool(
                env_map.get("PR_AGENT_CONTEXT_DELETE_COMMENT_WHEN_EMPTY"),
                default=True,
            ),
            skip_comment_on_readonly_token=_parse_bool(
                env_map.get("PR_AGENT_CONTEXT_SKIP_COMMENT_ON_READONLY_TOKEN"),
                default=True,
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
