from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

from pr_agent_context.constants import (
    DEFAULT_CHARACTERS_PER_LINE,
    DEFAULT_CHECK_SETTLE_POLL_INTERVAL_SECONDS,
    DEFAULT_CHECK_SETTLE_TIMEOUT_SECONDS,
    DEFAULT_COPILOT_AUTHOR_PATTERNS,
    DEFAULT_COVERAGE_ARTIFACT_PREFIX,
    DEFAULT_COVERAGE_REPORT_FILENAME,
    DEFAULT_COVERAGE_SELECTION_STRATEGY,
    DEFAULT_COVERAGE_SOURCE_CONCLUSIONS,
    DEFAULT_DEBUG_ARTIFACT_PREFIX,
    DEFAULT_EXECUTION_MODE,
    DEFAULT_FORK_BEHAVIOR,
    DEFAULT_INCLUDE_APPROVAL_GATED_ACTIONS_RUN_NOTES,
    DEFAULT_MAX_EXTERNAL_CHECKS,
    DEFAULT_MAX_FAILED_JOBS,
    DEFAULT_MAX_FAILED_RUNS,
    DEFAULT_MAX_FAILING_ITEMS,
    DEFAULT_MAX_LOG_LINES_PER_JOB,
    DEFAULT_MAX_REVIEW_THREADS,
    DEFAULT_PATCH_COVERAGE_SOURCE_MODE,
    DEFAULT_PUBLISH_ALL_CLEAR_COMMENTS_IN_REFRESH,
    DEFAULT_PUBLISH_MODE,
    DEFAULT_REVIEW_SETTLE_POLL_INTERVAL_SECONDS,
    DEFAULT_REVIEW_SETTLE_TIMEOUT_SECONDS,
    DEFAULT_TARGET_PATCH_COVERAGE,
    DEFAULT_TOOL_REF,
)

ExecutionMode = Literal["ci", "refresh", "auto"]
ResolvedExecutionMode = Literal["ci", "refresh"]
PublishMode = Literal["append", "update_latest_managed", "update_matching", "update_latest_scoped"]
CoverageSelectionStrategy = Literal["latest_successful"]
ForkBehavior = Literal["best_effort"]
PatchCoverageSourceMode = Literal["raw_coverage_artifacts", "coverage_xml_artifact"]


def _parse_bool(value: str | bool | None, *, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return value.strip().lower() in {"1", "true", "yes", "on"}


def parse_bool_env(value: str | bool | None, *, default: bool) -> bool:
    return _parse_bool(value, default=default)


def _resolve_target_patch_coverage(
    env_map: Mapping[str, str],
    *,
    workspace: Path,
) -> float:
    raw_override = env_map.get("PR_AGENT_CONTEXT_TARGET_PATCH_COVERAGE")
    if raw_override is not None and raw_override.strip():
        return float(raw_override)

    configured_target = _load_patch_target_from_repo_config(workspace)
    if configured_target is not None:
        return configured_target

    return DEFAULT_TARGET_PATCH_COVERAGE


def _load_patch_target_from_repo_config(workspace: Path) -> float | None:
    config_path = _find_codecov_config_file(workspace)
    if config_path is None:
        return None

    try:
        parsed = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return None

    return _extract_codecov_patch_target(parsed)


def _find_codecov_config_file(workspace: Path) -> Path | None:
    for name in (".codecov.yml", "codecov.yml", ".codecov.yaml", "codecov.yaml"):
        candidate = workspace / name
        if candidate.is_file():
            return candidate
    return None


def _extract_codecov_patch_target(data: object) -> float | None:
    if not isinstance(data, Mapping):
        return None

    coverage = data.get("coverage")
    if not isinstance(coverage, Mapping):
        return None

    status = coverage.get("status")
    if not isinstance(status, Mapping):
        return None

    patch = status.get("patch")
    if not isinstance(patch, Mapping):
        return None

    direct_target = _parse_percent_like_value(patch.get("target"))
    if direct_target is not None:
        return direct_target

    default_entry = patch.get("default")
    if isinstance(default_entry, Mapping):
        default_target = _parse_percent_like_value(default_entry.get("target"))
        if default_target is not None:
            return default_target

    for value in patch.values():
        if not isinstance(value, Mapping):
            continue
        nested_target = _parse_percent_like_value(value.get("target"))
        if nested_target is not None:
            return nested_target

    return None


def _parse_percent_like_value(value: object) -> float | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        numeric = float(value)
        if 0 <= numeric <= 1:
            return numeric * 100
        return numeric if 0 <= numeric <= 100 else None
    if not isinstance(value, str):
        return None

    raw = value.strip()
    if not raw:
        return None
    if raw.lower() == "auto":
        return None
    if raw.endswith("%"):
        raw = raw[:-1].strip()
    try:
        parsed = float(raw)
    except ValueError:
        return None
    return parsed if 0 <= parsed <= 100 else None


class PullRequestRef(BaseModel):
    model_config = ConfigDict(frozen=True)

    owner: str
    repo: str
    number: int
    base_sha: str
    head_sha: str


class TriggerContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    event_name: str
    action: str | None = None
    source: str
    label: str | None = None
    pull_request_number: int | None = None
    base_sha: str | None = None
    head_sha: str | None = None
    is_fork: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def _populate_label(cls, data: object) -> object:
        if not isinstance(data, dict):
            return data
        if data.get("label"):
            return data
        event_name = str(data.get("event_name") or "").strip() or "unknown"
        action_value = data.get("action")
        action = str(action_value).strip() or None if action_value is not None else None
        populated = dict(data)
        populated["label"] = _build_trigger_label(event_name, action)
        return populated


class CopilotAuthorMatcherConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    exact_logins: tuple[str, ...] = ()
    regex_patterns: tuple[str, ...] = ()

    def matches(self, author_login: str) -> bool:
        candidate_logins = _build_login_match_candidates(author_login)
        exact_logins = {
            candidate
            for configured_login in self.exact_logins
            for candidate in _build_login_match_candidates(configured_login)
        }
        if any(candidate in exact_logins for candidate in candidate_logins):
            return True
        return any(
            re.search(pattern, candidate, re.IGNORECASE)
            for pattern in self.regex_patterns
            for candidate in candidate_logins
        )


def _build_login_match_candidates(author_login: str) -> tuple[str, ...]:
    normalized = author_login.strip()
    if normalized.endswith("[bot]"):
        return (normalized, normalized[:-5])
    return (normalized,)


class RunConfig(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)

    github_api_url: str = "https://api.github.com"
    github_token: str
    tool_ref: str = DEFAULT_TOOL_REF
    repository_owner: str = ""
    repository_name: str = ""
    trigger: TriggerContext = Field(
        default_factory=lambda: TriggerContext(
            event_name="pull_request",
            action=None,
            source="pull_request",
            label="pull request updated",
        )
    )
    pull_request: PullRequestRef | None = None
    run_id: int
    run_attempt: int
    workspace: Path
    execution_mode: ResolvedExecutionMode = "ci"
    publish_mode: PublishMode = DEFAULT_PUBLISH_MODE
    include_refresh_metadata: bool = True
    include_review_comments: bool = True
    include_failing_checks: bool = True
    include_cross_run_failures: bool = True
    include_external_checks: bool = True
    include_approval_gated_actions_run_notes: bool = (
        DEFAULT_INCLUDE_APPROVAL_GATED_ACTIONS_RUN_NOTES
    )
    wait_for_checks_to_settle: bool = True
    wait_for_reviews_to_settle: bool = False
    publish_all_clear_comments_in_refresh: bool = DEFAULT_PUBLISH_ALL_CLEAR_COMMENTS_IN_REFRESH
    hide_previous_managed_comments_on_append: bool = True
    include_patch_coverage: bool = True
    enable_cross_run_coverage_lookup: bool = True
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
    review_settle_timeout_seconds: int = DEFAULT_REVIEW_SETTLE_TIMEOUT_SECONDS
    review_settle_poll_interval_seconds: int = DEFAULT_REVIEW_SETTLE_POLL_INTERVAL_SECONDS
    characters_per_line: int = DEFAULT_CHARACTERS_PER_LINE
    target_patch_coverage: float = DEFAULT_TARGET_PATCH_COVERAGE
    patch_coverage_source_mode: PatchCoverageSourceMode = DEFAULT_PATCH_COVERAGE_SOURCE_MODE
    coverage_artifact_prefix: str = DEFAULT_COVERAGE_ARTIFACT_PREFIX
    coverage_report_artifact_name: str = ""
    coverage_report_filename: str = DEFAULT_COVERAGE_REPORT_FILENAME
    coverage_source_workflows: tuple[str, ...] = ()
    coverage_source_conclusions: tuple[str, ...] = DEFAULT_COVERAGE_SOURCE_CONCLUSIONS
    coverage_selection_strategy: CoverageSelectionStrategy = DEFAULT_COVERAGE_SELECTION_STRATEGY
    fork_behavior: ForkBehavior = DEFAULT_FORK_BEHAVIOR
    delete_comment_when_empty: bool = True
    skip_comment_on_readonly_token: bool = True
    coverage_artifacts_dir: Path | None = Field(default=None)
    debug_artifacts_dir: Path | None = Field(default=None)
    github_output_path: Path | None = Field(default=None)

    @property
    def repository(self) -> str:
        if self.repository_owner and self.repository_name:
            return f"{self.repository_owner}/{self.repository_name}"
        if self.pull_request is not None:
            return f"{self.pull_request.owner}/{self.pull_request.repo}"
        return ""

    @model_validator(mode="after")
    def _validate_patch_coverage_source_config(self) -> RunConfig:
        if (
            self.patch_coverage_source_mode == "coverage_xml_artifact"
            and not self.coverage_report_artifact_name
        ):
            raise ValueError(
                "coverage_report_artifact_name is required when "
                "patch_coverage_source_mode=coverage_xml_artifact."
            )
        return self

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> RunConfig:
        env_map = dict(os.environ if env is None else env)
        owner, repo = _extract_repository(env_map)
        trigger = load_trigger_context_from_env(env_map)
        workspace = Path(env_map.get("PR_AGENT_CONTEXT_WORKSPACE", os.getcwd()))
        debug_artifacts = _parse_bool(
            env_map.get("PR_AGENT_CONTEXT_DEBUG_ARTIFACTS"),
            default=True,
        )
        execution_mode_requested = (
            env_map.get(
                "PR_AGENT_CONTEXT_EXECUTION_MODE",
                DEFAULT_EXECUTION_MODE,
            ).strip()
            or DEFAULT_EXECUTION_MODE
        )
        execution_mode = _resolve_execution_mode(
            execution_mode_requested,
            trigger.event_name,
        )

        pull_request = None
        if trigger.pull_request_number is not None and trigger.base_sha and trigger.head_sha:
            pull_request = PullRequestRef(
                owner=owner,
                repo=repo,
                number=trigger.pull_request_number,
                base_sha=trigger.base_sha,
                head_sha=trigger.head_sha,
            )

        return cls(
            github_api_url=env_map.get("GITHUB_API_URL", "https://api.github.com"),
            github_token=env_map["GITHUB_TOKEN"],
            tool_ref=env_map.get("PR_AGENT_CONTEXT_TOOL_REF", DEFAULT_TOOL_REF).strip()
            or DEFAULT_TOOL_REF,
            repository_owner=owner,
            repository_name=repo,
            trigger=trigger,
            pull_request=pull_request,
            run_id=int(env_map["GITHUB_RUN_ID"]),
            run_attempt=int(env_map.get("GITHUB_RUN_ATTEMPT", "1")),
            workspace=workspace,
            execution_mode=execution_mode,
            publish_mode=_parse_publish_mode(
                env_map.get("PR_AGENT_CONTEXT_PUBLISH_MODE", DEFAULT_PUBLISH_MODE)
            ),
            include_refresh_metadata=_parse_bool(
                env_map.get("PR_AGENT_CONTEXT_INCLUDE_REFRESH_METADATA"),
                default=True,
            ),
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
            include_approval_gated_actions_run_notes=_parse_bool(
                env_map.get("PR_AGENT_CONTEXT_INCLUDE_APPROVAL_GATED_ACTIONS_RUN_NOTES"),
                default=DEFAULT_INCLUDE_APPROVAL_GATED_ACTIONS_RUN_NOTES,
            ),
            wait_for_checks_to_settle=_parse_bool(
                env_map.get("PR_AGENT_CONTEXT_WAIT_FOR_CHECKS_TO_SETTLE"),
                default=True,
            ),
            wait_for_reviews_to_settle=_parse_bool(
                env_map.get("PR_AGENT_CONTEXT_WAIT_FOR_REVIEWS_TO_SETTLE"),
                default=False,
            ),
            publish_all_clear_comments_in_refresh=_parse_bool(
                env_map.get("PR_AGENT_CONTEXT_PUBLISH_ALL_CLEAR_COMMENTS_IN_REFRESH"),
                default=DEFAULT_PUBLISH_ALL_CLEAR_COMMENTS_IN_REFRESH,
            ),
            hide_previous_managed_comments_on_append=_parse_bool(
                env_map.get("PR_AGENT_CONTEXT_HIDE_PREVIOUS_MANAGED_COMMENTS_ON_APPEND"),
                default=True,
            ),
            include_patch_coverage=_parse_bool(
                env_map.get("PR_AGENT_CONTEXT_INCLUDE_PATCH_COVERAGE"),
                default=True,
            ),
            enable_cross_run_coverage_lookup=_parse_bool(
                env_map.get("PR_AGENT_CONTEXT_ENABLE_CROSS_RUN_COVERAGE_LOOKUP"),
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
            review_settle_timeout_seconds=int(
                env_map.get(
                    "PR_AGENT_CONTEXT_REVIEW_SETTLE_TIMEOUT_SECONDS",
                    str(DEFAULT_REVIEW_SETTLE_TIMEOUT_SECONDS),
                )
            ),
            review_settle_poll_interval_seconds=int(
                env_map.get(
                    "PR_AGENT_CONTEXT_REVIEW_SETTLE_POLL_INTERVAL_SECONDS",
                    str(DEFAULT_REVIEW_SETTLE_POLL_INTERVAL_SECONDS),
                )
            ),
            characters_per_line=int(
                env_map.get(
                    "PR_AGENT_CONTEXT_CHARACTERS_PER_LINE",
                    str(DEFAULT_CHARACTERS_PER_LINE),
                )
            ),
            target_patch_coverage=_resolve_target_patch_coverage(
                env_map,
                workspace=workspace,
            ),
            patch_coverage_source_mode=_parse_patch_coverage_source_mode(
                env_map.get(
                    "PR_AGENT_CONTEXT_PATCH_COVERAGE_SOURCE_MODE",
                    DEFAULT_PATCH_COVERAGE_SOURCE_MODE,
                )
            ),
            coverage_artifact_prefix=env_map.get(
                "PR_AGENT_CONTEXT_COVERAGE_ARTIFACT_PREFIX",
                DEFAULT_COVERAGE_ARTIFACT_PREFIX,
            ).strip()
            or DEFAULT_COVERAGE_ARTIFACT_PREFIX,
            coverage_report_artifact_name=(
                env_map.get("PR_AGENT_CONTEXT_COVERAGE_REPORT_ARTIFACT_NAME", "").strip()
            ),
            coverage_report_filename=(
                env_map.get(
                    "PR_AGENT_CONTEXT_COVERAGE_REPORT_FILENAME",
                    DEFAULT_COVERAGE_REPORT_FILENAME,
                ).strip()
                or DEFAULT_COVERAGE_REPORT_FILENAME
            ),
            coverage_source_workflows=tuple(
                _split_pattern_entries(env_map.get("PR_AGENT_CONTEXT_COVERAGE_SOURCE_WORKFLOWS"))
            ),
            coverage_source_conclusions=tuple(
                _split_pattern_entries(env_map.get("PR_AGENT_CONTEXT_COVERAGE_SOURCE_CONCLUSIONS"))
                or list(DEFAULT_COVERAGE_SOURCE_CONCLUSIONS)
            ),
            coverage_selection_strategy=_parse_coverage_selection_strategy(
                env_map.get(
                    "PR_AGENT_CONTEXT_COVERAGE_SELECTION_STRATEGY",
                    DEFAULT_COVERAGE_SELECTION_STRATEGY,
                )
            ),
            fork_behavior=_parse_fork_behavior(
                env_map.get("PR_AGENT_CONTEXT_FORK_BEHAVIOR", DEFAULT_FORK_BEHAVIOR)
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


def _extract_repository(env: Mapping[str, str]) -> tuple[str, str]:
    repository = env["GITHUB_REPOSITORY"]
    owner, repo = repository.split("/", maxsplit=1)
    return owner, repo


def load_pull_request_context_from_env(
    env: Mapping[str, str],
) -> tuple[str, str, PullRequestRef]:
    owner, repo = _extract_repository(env)
    trigger = load_trigger_context_from_env(env)
    if trigger.pull_request_number is None or not trigger.base_sha or not trigger.head_sha:
        raise ValueError("Unable to determine pull request context from event payload.")
    return (
        owner,
        repo,
        PullRequestRef(
            owner=owner,
            repo=repo,
            number=trigger.pull_request_number,
            base_sha=trigger.base_sha,
            head_sha=trigger.head_sha,
        ),
    )


def load_trigger_context_from_env(env: Mapping[str, str]) -> TriggerContext:
    event = _load_event_payload(env["GITHUB_EVENT_PATH"])
    event_name = (
        env.get("PR_AGENT_CONTEXT_TRIGGER_EVENT_NAME") or env.get("GITHUB_EVENT_NAME") or "unknown"
    ).strip()
    action = (env.get("PR_AGENT_CONTEXT_TRIGGER_EVENT_ACTION") or "").strip() or None
    source = _build_trigger_source(event_name, action)
    return _extract_trigger_context(event_name, action, source, event)


def _extract_trigger_context(
    event_name: str,
    action: str | None,
    source: str,
    event: Mapping[str, Any],
) -> TriggerContext:
    if pull_request := _extract_pull_request_mapping(event):
        number = _extract_pull_request_number_if_present(event)
        base_sha, head_sha = _extract_shas_from_pull_request_mapping(pull_request)
        return TriggerContext(
            event_name=event_name,
            action=action,
            source=source,
            pull_request_number=number,
            base_sha=base_sha,
            head_sha=head_sha,
            is_fork=_extract_is_fork(pull_request),
        )

    if event_name == "workflow_run":
        workflow_run = event.get("workflow_run")
        if isinstance(workflow_run, Mapping):
            number = None
            pull_requests = workflow_run.get("pull_requests")
            if isinstance(pull_requests, list) and pull_requests:
                first = pull_requests[0]
                if isinstance(first, Mapping) and first.get("number") is not None:
                    number = int(first["number"])
            head_sha = str(workflow_run.get("head_sha") or "").strip() or None
            return TriggerContext(
                event_name=event_name,
                action=action,
                source=source,
                pull_request_number=number,
                head_sha=head_sha,
            )

    if event_name == "status":
        head_sha = str(event.get("sha") or "").strip() or None
        return TriggerContext(
            event_name=event_name,
            action=action,
            source=source,
            head_sha=head_sha,
        )

    if event_name in {"check_run", "check_suite"}:
        check = event.get(event_name)
        if isinstance(check, Mapping):
            head_sha = str(check.get("head_sha") or "").strip() or None
            pull_requests = check.get("pull_requests")
            number = None
            if isinstance(pull_requests, list) and pull_requests:
                first = pull_requests[0]
                if isinstance(first, Mapping) and first.get("number") is not None:
                    number = int(first["number"])
            return TriggerContext(
                event_name=event_name,
                action=action,
                source=source,
                pull_request_number=number,
                head_sha=head_sha,
            )

    return TriggerContext(
        event_name=event_name,
        action=action,
        source=source,
    )


def _extract_pull_request_mapping(event: Mapping[str, Any]) -> Mapping[str, Any] | None:
    pull_request = event.get("pull_request")
    if isinstance(pull_request, Mapping):
        return pull_request
    return None


def _extract_pull_request_number_if_present(event: Mapping[str, Any]) -> int | None:
    pull_request = event.get("pull_request")
    if isinstance(pull_request, Mapping) and pull_request.get("number") is not None:
        return int(pull_request["number"])
    if event.get("number") is not None:
        return int(event["number"])
    return None


def _extract_pull_request_number(event: Mapping[str, Any]) -> int:
    number = _extract_pull_request_number_if_present(event)
    if number is None:
        raise ValueError("Unable to determine pull request number from event payload.")
    return number


def _extract_shas_from_pull_request_mapping(
    pull_request: Mapping[str, Any],
) -> tuple[str | None, str | None]:
    base = pull_request.get("base")
    head = pull_request.get("head")
    if not isinstance(base, Mapping) or not isinstance(head, Mapping):
        return None, None
    base_sha = str(base.get("sha") or "").strip() or None
    head_sha = str(head.get("sha") or "").strip() or None
    return base_sha, head_sha


def _extract_pull_request_shas(event: Mapping[str, Any]) -> tuple[str, str]:
    pull_request = _extract_pull_request_mapping(event)
    if pull_request is None:
        raise ValueError("Unable to determine pull request SHAs from event payload.")
    base = pull_request.get("base")
    head = pull_request.get("head")
    if not isinstance(base, Mapping) or not isinstance(head, Mapping):
        raise ValueError("Unable to determine pull request SHAs from event payload.")
    base_sha, head_sha = _extract_shas_from_pull_request_mapping(pull_request)
    if not base_sha or not head_sha:
        raise ValueError("Pull request event is missing base/head SHAs.")
    return base_sha, head_sha


def _extract_is_fork(pull_request: Mapping[str, Any]) -> bool | None:
    head = pull_request.get("head")
    if not isinstance(head, Mapping):
        return None
    repo = head.get("repo")
    if not isinstance(repo, Mapping):
        return None
    if repo.get("fork") is None:
        return None
    return bool(repo["fork"])


def _resolve_execution_mode(
    requested_mode: str,
    event_name: str,
) -> ResolvedExecutionMode:
    normalized = requested_mode.strip().lower()
    if normalized not in {"auto", "ci", "refresh"}:
        raise ValueError(f"Unsupported execution mode: {requested_mode}")
    if normalized == "ci":
        return "ci"
    if normalized == "refresh":
        return "refresh"
    if event_name in {
        "pull_request_review",
        "pull_request_review_comment",
        "workflow_run",
        "status",
        "check_run",
        "check_suite",
    }:
        return "refresh"
    return "ci"


def _build_trigger_source(event_name: str, action: str | None) -> str:
    if action:
        return f"{event_name}:{action}"
    return event_name


def _build_trigger_label(event_name: str, action: str | None) -> str:
    normalized_event = (event_name or "unknown").strip()
    normalized_action = action.strip() if action else None

    if normalized_event == "pull_request":
        if normalized_action in {"opened", "reopened"}:
            return "pull request opened"
        if normalized_action == "synchronize":
            return "commit pushed"
        return "pull request updated"

    if normalized_event == "pull_request_review":
        action_labels = {
            "submitted": "review posted",
            "edited": "review edited",
            "dismissed": "review dismissed",
        }
        return action_labels.get(normalized_action or "", "review updated")

    if normalized_event == "pull_request_review_comment":
        action_labels = {
            "created": "review comment posted",
            "edited": "review comment edited",
            "deleted": "review comment deleted",
        }
        return action_labels.get(normalized_action or "", "review comment updated")

    if normalized_event in {"check_run", "check_suite"}:
        return "check completed" if normalized_action == "completed" else "check updated"

    if normalized_event == "workflow_run":
        return "workflow completed" if normalized_action == "completed" else "workflow updated"

    if normalized_event == "status":
        return "status updated"

    fallback_parts = [normalized_event.replace("_", " ")]
    if normalized_action:
        fallback_parts.append(normalized_action.replace("_", " "))
    return " ".join(part for part in fallback_parts if part).strip() or "unknown trigger"


def _parse_publish_mode(value: str | None) -> PublishMode:
    normalized = (value or DEFAULT_PUBLISH_MODE).strip()
    if normalized not in {
        "append",
        "update_latest_managed",
        "update_matching",
        "update_latest_scoped",
    }:
        raise ValueError(f"Unsupported publish mode: {value}")
    return normalized  # type: ignore[return-value]


def _parse_coverage_selection_strategy(value: str | None) -> CoverageSelectionStrategy:
    normalized = (value or DEFAULT_COVERAGE_SELECTION_STRATEGY).strip()
    if normalized != "latest_successful":
        raise ValueError(f"Unsupported coverage selection strategy: {value}")
    return normalized


def _parse_patch_coverage_source_mode(value: str | None) -> PatchCoverageSourceMode:
    normalized = (value or DEFAULT_PATCH_COVERAGE_SOURCE_MODE).strip()
    if normalized not in {"raw_coverage_artifacts", "coverage_xml_artifact"}:
        raise ValueError(f"Unsupported patch coverage source mode: {value}")
    return normalized


def _parse_fork_behavior(value: str | None) -> ForkBehavior:
    normalized = (value or DEFAULT_FORK_BEHAVIOR).strip()
    if normalized != "best_effort":
        raise ValueError(f"Unsupported fork behavior: {value}")
    return normalized


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
