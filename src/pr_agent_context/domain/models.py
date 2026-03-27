from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from pr_agent_context.config import PullRequestRef, TriggerContext


class ReviewMessage(BaseModel):
    model_config = ConfigDict(frozen=True)

    comment_id: int
    author_login: str
    author_type: str | None = None
    body: str
    created_at: datetime | None = None
    updated_at: datetime | None = None
    url: str


class ReviewThread(BaseModel):
    model_config = ConfigDict(frozen=True)

    thread_id: int | str
    sort_key: int | None = None
    classifier: Literal["copilot", "review"]
    path: str | None = None
    line: int | None = None
    start_line: int | None = None
    original_line: int | None = None
    is_resolved: bool = False
    is_outdated: bool = False
    url: str
    messages: list[ReviewMessage] = Field(default_factory=list)
    item_id: str | None = None


def review_thread_sort_key(thread: ReviewThread) -> tuple[float | int, int, int, str]:
    sort_key = thread.sort_key if thread.sort_key is not None else float("inf")
    if isinstance(thread.thread_id, int):
        return (sort_key, 0, thread.thread_id, "")
    return (sort_key, 1, 0, thread.thread_id)


class FailingCheck(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_type: Literal[
        "actions_job",
        "actions_workflow_run",
        "external_check_run",
        "commit_status",
    ] = "actions_job"
    job_id: int | None = None
    workflow_name: str = "Workflow"
    job_name: str = "Job"
    matrix_label: str | None = None
    app_name: str | None = None
    context_name: str | None = None
    summary: str | None = None
    status: str | None = None
    conclusion: str | None = None
    url: str
    failed_steps: list[str] = Field(default_factory=list)
    excerpt_lines: list[str] = Field(default_factory=list)
    head_sha: str | None = None
    is_current_run: bool = False
    logs_available: bool = False
    details_available: bool = False
    dedupe_key: str | None = None
    observed_at: datetime | None = None
    run_id: int | None = None
    run_attempt: int | None = None
    run_number: int | None = None
    item_id: str | None = None


def failing_check_sort_key(
    failure: FailingCheck,
) -> tuple[int, int, str, str, str, int, str]:
    source_order = {
        "actions_job": 0,
        "actions_workflow_run": 1,
        "external_check_run": 2,
        "commit_status": 3,
    }
    return (
        source_order[failure.source_type],
        0 if failure.is_current_run else 1,
        failure.workflow_name,
        failure.job_name,
        failure.matrix_label or failure.app_name or failure.context_name or "",
        0 - (failure.run_number or 0),
        failure.url,
    )


class TruncationNote(BaseModel):
    model_config = ConfigDict(frozen=True)

    target: str
    strategy: str
    message: str
    original_size: int
    truncated_size: int


class CoverageFileGap(BaseModel):
    model_config = ConfigDict(frozen=True)

    path: str
    changed_added_lines: list[int] = Field(default_factory=list)
    changed_executable_lines: list[int] = Field(default_factory=list)
    covered_changed_executable_lines: list[int] = Field(default_factory=list)
    uncovered_changed_executable_lines: list[int] = Field(default_factory=list)
    has_measured_data: bool = False


class PatchCoverageSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    target_percent: float
    actual_percent: float | None = None
    total_changed_executable_lines: int = 0
    covered_changed_executable_lines: int = 0
    files: list[CoverageFileGap] = Field(default_factory=list)
    actionable: bool = False
    is_na: bool = False


class CollectedContext(BaseModel):
    model_config = ConfigDict(frozen=True)

    trigger: TriggerContext
    pull_request: PullRequestRef
    review_threads: list[ReviewThread] = Field(default_factory=list)
    failing_checks: list[FailingCheck] = Field(default_factory=list)
    patch_coverage: PatchCoverageSummary | None = None
    failing_check_debug: dict | None = None
    review_settlement_debug: dict | None = None
    coverage_source_debug: dict | None = None


class ManagedComment(BaseModel):
    model_config = ConfigDict(frozen=True)

    comment_id: int
    node_id: str | None = None
    is_minimized: bool | None = None
    author_login: str
    author_type: str | None = None
    body: str
    url: str
    created_at: datetime | None = None
    updated_at: datetime | None = None
    marker: ManagedCommentIdentity | None = None


class ManagedCommentIdentity(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: str = "v5"
    pull_request_number: int
    publish_mode: Literal[
        "append", "update_latest_managed", "update_matching", "update_latest_scoped"
    ] = "append"
    execution_mode: Literal["ci", "refresh"] | None = None
    head_sha: str
    trigger_event_name: str = "pull_request"
    generated_at: str = "unknown"
    tool_ref: str
    run_id: int | None = None
    run_attempt: int | None = None


PublicationAction = Literal[
    "none",
    "deleted",
    "noop_no_comment",
    "created",
    "updated_latest_managed",
    "updated_latest_scoped",
    "updated_matching",
    "unchanged_latest_managed",
    "unchanged_latest_scoped",
    "unchanged_matching",
    "skipped_forbidden",
]


class TemplateDiagnostics(BaseModel):
    model_config = ConfigDict(frozen=True)

    template_source: Literal["built_in", "file"]
    template_path: str | None = None
    placeholders_used: list[str] = Field(default_factory=list)
    prompt_preamble_inserted: bool = False


class RenderedPrompt(BaseModel):
    model_config = ConfigDict(frozen=True)

    prompt_markdown: str
    comment_body: str
    prompt_sha256: str
    has_actionable_items: bool
    should_publish_comment: bool
    truncation_notes: list[TruncationNote] = Field(default_factory=list)
    template_diagnostics: TemplateDiagnostics


class DebugSummary(BaseModel):
    model_config = ConfigDict(frozen=True)

    tool_ref: str
    unresolved_thread_count: int
    failing_check_count: int
    failing_check_source_counts: dict[str, int] = Field(default_factory=dict)
    patch_coverage_percent: float | None = None
    has_actionable_items: bool
    should_publish_comment: bool
    comment_written: bool
    comment_id: int | None = None
    comment_url: str | None = None
    prompt_sha256: str
    truncation_count: int = 0


class PublicationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    comment_id: int | None = None
    comment_url: str | None = None
    comment_written: bool = False
    action: PublicationAction = "none"
    managed_comment_count: int = 0
    body_changed: bool = False
    skipped_reason: str | None = None
    error_status_code: int | None = None
    publish_mode: Literal[
        "append", "update_latest_managed", "update_matching", "update_latest_scoped"
    ] = "append"
    run_id: int | None = None
    run_attempt: int | None = None
    head_sha: str | None = None
    trigger_event_name: str | None = None
    matched_existing_comment: bool = False
    matched_comment_id: int | None = None
    matched_comment_run_id: int | None = None
    matched_comment_run_attempt: int | None = None
    sync_debug: dict[str, object] = Field(default_factory=dict)
