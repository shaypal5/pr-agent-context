from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from pr_agent_context.constants import (
    DEFAULT_CHARACTERS_PER_LINE,
    DEFAULT_CHECK_SETTLE_POLL_INTERVAL_SECONDS,
    DEFAULT_CHECK_SETTLE_TIMEOUT_SECONDS,
    DEFAULT_COPILOT_AUTHOR_PATTERNS,
    DEFAULT_COVERAGE_ARTIFACT_PREFIX,
    DEFAULT_DEBUG_ARTIFACT_PREFIX,
    DEFAULT_MAX_EXTERNAL_CHECKS,
    DEFAULT_MAX_FAILED_JOBS,
    DEFAULT_MAX_FAILED_RUNS,
    DEFAULT_MAX_FAILING_ITEMS,
    DEFAULT_MAX_LOG_LINES_PER_JOB,
    DEFAULT_MAX_REVIEW_THREADS,
    DEFAULT_TARGET_PATCH_COVERAGE,
    DEFAULT_TOOL_REF,
)


def _parse_bool(value: str | bool | None, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return value.strip().lower() in {"1", "true", "yes", "on"}


def parse_bool_env(value: str | bool | None, *, default: bool) -> bool:
    return _parse_bool(value, default=default)


class PullRequestRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    owner: str
    repo: str
    number: int
    base_sha: str
    head_sha: str


class CopilotAuthorMatcherConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    exact_logins: tuple[str, ...] = ()
    regex_patterns: tuple[str, ...] = ()

    def matches(self, author_login: str) -> bool:
        if author_login in self.exact_logins:
            return True
        return any(
            re.search(pattern, author_login, re.IGNORECASE) for pattern in self.regex_patterns
        )


class RunConfig(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    github_api_url: str = "https://api.github.com"
    github_token: str
    tool_ref: str = DEFAULT_TOOL_REF
    pull_request: PullRequestRef
    run_id: int
    run_attempt: int
    workspace: Path
    include_review_comments: bool = True
    include_failing_checks: bool = True
    include_cross_run_failures: bool = True
    include_external_checks: bool = True
    wait_for_checks_to_settle: bool = True
    include_patch_coverage: bool = True
    force_patch_coverage_section: bool = False
    prompt_preamble: str = ""
    prompt_template_file: Path | None = None
    debug_artifacts: bool = True
    debug_artifact_prefix: str = DEFAULT_DEBUG_ARTIFACT_PREFIX
    copilot_author_patterns: CopilotAuthorMatcherConfig = Field(
        default_factory=lambda: _parse_copilot_author_patterns(None)
    )
    max_review_threads: int = DEFAULT_MAX_REVIEW_THREADS
    max_actions_runs: int = DEFAULT_MAX_FAILED_RUNS
    max_actions_jobs: int = DEFAULT_MAX_FAILED_JOBS
    max_external_checks: int = DEFAULT_MAX_EXTERNAL_CHECKS
    max_failing_checks: int = DEFAULT_MAX_FAILING_ITEMS
    max_log_lines_per_job: int = DEFAULT_MAX_LOG_LINES_PER_JOB
    check_settle_timeout_seconds: int = DEFAULT_CHECK_SETTLE_TIMEOUT_SECONDS
    check_settle_poll_interval_seconds: int = DEFAULT_CHECK_SETTLE_POLL_INTERVAL_SECONDS
    characters_per_line: int = DEFAULT_CHARACTERS_PER_LINE
    target_patch_coverage: float = DEFAULT_TARGET_PATCH_COVERAGE
    coverage_artifact_prefix: str = DEFAULT_COVERAGE_ARTIFACT_PREFIX
    delete_comment_when_empty: bool = True
    skip_comment_on_readonly_token: bool = True
    coverage_artifacts_dir: Path | None = Field(default=None)
    debug_artifacts_dir: Path | None = Field(default=None)
    github_output_path: Path | None = Field(default=None)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> RunConfig:
        env_map = dict(os.environ if env is None else env)
        owner, repo, pull_request = load_pull_request_context_from_env(env_map)
        workspace = Path(env_map.get("PR_AGENT_CONTEXT_WORKSPACE", os.getcwd()))
        debug_artifacts = _parse_bool(
            env_map.get("PR_AGENT_CONTEXT_DEBUG_ARTIFACTS"),
            default=True,
        )

        return cls(
            github_api_url=env_map.get("GITHUB_API_URL", "https://api.github.com"),
            github_token=env_map["GITHUB_TOKEN"],
            tool_ref=env_map.get("PR_AGENT_CONTEXT_TOOL_REF", DEFAULT_TOOL_REF).strip()
            or DEFAULT_TOOL_REF,
            pull_request=pull_request,
            run_id=int(env_map["GITHUB_RUN_ID"]),
            run_attempt=int(env_map.get("GITHUB_RUN_ATTEMPT", "1")),
            workspace=workspace,
            include_review_comments=_parse_bool(
                env_map.get("PR_AGENT_CONTEXT_INCLUDE_REVIEW_COMMENTS"),
                default=True,
            ),
            include_failing_checks=_parse_bool(
                env_map.get("PR_AGENT_CONTEXT_INCLUDE_FAILING_CHECKS"),
                default=True,
            ),
            include_cross_run_failures=_parse_bool(
                env_map.get("PR_AGENT_CONTEXT_INCLUDE_CROSS_RUN_FAILURES"),
                default=True,
            ),
            include_external_checks=_parse_bool(
                env_map.get("PR_AGENT_CONTEXT_INCLUDE_EXTERNAL_CHECKS"),
                default=True,
            ),
            wait_for_checks_to_settle=_parse_bool(
                env_map.get("PR_AGENT_CONTEXT_WAIT_FOR_CHECKS_TO_SETTLE"),
                default=True,
            ),
            include_patch_coverage=_parse_bool(
                env_map.get("PR_AGENT_CONTEXT_INCLUDE_PATCH_COVERAGE"),
                default=True,
            ),
            force_patch_coverage_section=_parse_bool(
                env_map.get("PR_AGENT_CONTEXT_FORCE_PATCH_COVERAGE_SECTION"),
                default=False,
            ),
            prompt_preamble=env_map.get("PR_AGENT_CONTEXT_PROMPT_PREAMBLE", "").strip(),
            prompt_template_file=_resolve_workspace_path(
                workspace,
                env_map.get("PR_AGENT_CONTEXT_PROMPT_TEMPLATE_FILE"),
            ),
            debug_artifacts=debug_artifacts,
            debug_artifact_prefix=env_map.get(
                "PR_AGENT_CONTEXT_DEBUG_ARTIFACT_PREFIX",
                DEFAULT_DEBUG_ARTIFACT_PREFIX,
            ).strip()
            or DEFAULT_DEBUG_ARTIFACT_PREFIX,
            copilot_author_patterns=_parse_copilot_author_patterns(
                env_map.get("PR_AGENT_CONTEXT_COPILOT_AUTHOR_PATTERNS")
            ),
            max_review_threads=int(
                env_map.get(
                    "PR_AGENT_CONTEXT_MAX_REVIEW_THREADS",
                    str(DEFAULT_MAX_REVIEW_THREADS),
                )
            ),
            max_actions_jobs=int(
                env_map.get(
                    "PR_AGENT_CONTEXT_MAX_ACTIONS_JOBS",
                    str(DEFAULT_MAX_FAILED_JOBS),
                )
            ),
            max_actions_runs=int(
                env_map.get(
                    "PR_AGENT_CONTEXT_MAX_ACTIONS_RUNS",
                    str(DEFAULT_MAX_FAILED_RUNS),
                )
            ),
            max_external_checks=int(
                env_map.get(
                    "PR_AGENT_CONTEXT_MAX_EXTERNAL_CHECKS",
                    str(DEFAULT_MAX_EXTERNAL_CHECKS),
                )
            ),
            max_failing_checks=int(
                env_map.get(
                    "PR_AGENT_CONTEXT_MAX_FAILING_CHECKS",
                    str(DEFAULT_MAX_FAILING_ITEMS),
                )
            ),
            max_log_lines_per_job=int(
                env_map.get(
                    "PR_AGENT_CONTEXT_MAX_LOG_LINES_PER_JOB",
                    str(DEFAULT_MAX_LOG_LINES_PER_JOB),
                )
            ),
            check_settle_timeout_seconds=int(
                env_map.get(
                    "PR_AGENT_CONTEXT_CHECK_SETTLE_TIMEOUT_SECONDS",
                    str(DEFAULT_CHECK_SETTLE_TIMEOUT_SECONDS),
                )
            ),
            check_settle_poll_interval_seconds=int(
                env_map.get(
                    "PR_AGENT_CONTEXT_CHECK_SETTLE_POLL_INTERVAL_SECONDS",
                    str(DEFAULT_CHECK_SETTLE_POLL_INTERVAL_SECONDS),
                )
            ),
            characters_per_line=int(
                env_map.get(
                    "PR_AGENT_CONTEXT_CHARACTERS_PER_LINE",
                    str(DEFAULT_CHARACTERS_PER_LINE),
                )
            ),
            target_patch_coverage=float(
                env_map.get(
                    "PR_AGENT_CONTEXT_TARGET_PATCH_COVERAGE",
                    str(DEFAULT_TARGET_PATCH_COVERAGE),
                )
            ),
            coverage_artifact_prefix=env_map.get(
                "PR_AGENT_CONTEXT_COVERAGE_ARTIFACT_PREFIX",
                DEFAULT_COVERAGE_ARTIFACT_PREFIX,
            ).strip()
            or DEFAULT_COVERAGE_ARTIFACT_PREFIX,
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
            debug_artifacts_dir=(
                Path(debug_artifacts_dir.strip())
                if (debug_artifacts_dir := env_map.get("PR_AGENT_CONTEXT_DEBUG_ARTIFACTS_DIR"))
                and debug_artifacts_dir.strip()
                else (workspace / "pr-agent-context-debug" if debug_artifacts else None)
            ),
            github_output_path=(
                Path(env_map["GITHUB_OUTPUT"]) if env_map.get("GITHUB_OUTPUT") else None
            ),
        )


def _load_event_payload(path: str) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_pull_request_context_from_env(
    env: Mapping[str, str],
) -> tuple[str, str, PullRequestRef]:
    repository = env["GITHUB_REPOSITORY"]
    owner, repo = repository.split("/", maxsplit=1)
    event = _load_event_payload(env["GITHUB_EVENT_PATH"])
    pull_request_number = _extract_pull_request_number(event)
    base_sha, head_sha = _extract_pull_request_shas(event)
    return (
        owner,
        repo,
        PullRequestRef(
            owner=owner,
            repo=repo,
            number=pull_request_number,
            base_sha=base_sha,
            head_sha=head_sha,
        ),
    )


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


def _parse_copilot_author_patterns(value: str | None) -> CopilotAuthorMatcherConfig:
    entries = _split_pattern_entries(value) or list(DEFAULT_COPILOT_AUTHOR_PATTERNS)
    exact_logins: list[str] = []
    regex_patterns: list[str] = []
    for entry in entries:
        if entry.startswith("re:"):
            pattern = entry[3:].strip()
            if not pattern:
                raise ValueError("Empty regex pattern in copilot_author_patterns.")
            re.compile(pattern)
            regex_patterns.append(pattern)
            continue
        exact_logins.append(entry)
    return CopilotAuthorMatcherConfig(
        exact_logins=tuple(sorted(set(exact_logins))),
        regex_patterns=tuple(sorted(set(regex_patterns))),
    )


def _split_pattern_entries(value: str | None) -> list[str]:
    if value is None:
        return []
    raw_entries = [entry.strip() for part in value.splitlines() for entry in part.split(",")]
    return [entry for entry in raw_entries if entry]


def _resolve_workspace_path(workspace: Path, raw_path: str | None) -> Path | None:
    if raw_path is None or not raw_path.strip():
        return None
    workspace_root = workspace.resolve()
    candidate = Path(raw_path.strip())
    if not candidate.is_absolute():
        candidate = workspace_root / candidate
    resolved = candidate.resolve()
    try:
        resolved.relative_to(workspace_root)
    except ValueError as exc:
        raise ValueError(f"Configured path must be within the workspace: {raw_path}") from exc
    if not resolved.exists():
        raise ValueError(f"Configured path does not exist: {raw_path}")
    return resolved
