from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from pr_agent_context.config import PullRequestRef


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


class WorkflowFailure(BaseModel):
    model_config = ConfigDict(frozen=True)

    job_id: int
    workflow_name: str
    job_name: str
    matrix_label: str | None = None
    conclusion: str | None = None
    url: str
    failed_steps: list[str] = Field(default_factory=list)
    excerpt_lines: list[str] = Field(default_factory=list)
    item_id: str | None = None


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

    pull_request: PullRequestRef
    review_threads: list[ReviewThread] = Field(default_factory=list)
    workflow_failures: list[WorkflowFailure] = Field(default_factory=list)
    patch_coverage: PatchCoverageSummary | None = None


class ManagedComment(BaseModel):
    model_config = ConfigDict(frozen=True)

    comment_id: int
    author_login: str
    author_type: str | None = None
    body: str
    url: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


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
    failed_job_count: int
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
    action: str = "none"
    existing_managed_comment_count: int = 0
    duplicate_managed_comment_count: int = 0
    body_changed: bool = False
    skipped_reason: str | None = None
    error_status_code: int | None = None
