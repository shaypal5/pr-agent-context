from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


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

    thread_id: int
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


class ManagedComment(BaseModel):
    model_config = ConfigDict(frozen=True)

    comment_id: int
    author_login: str
    author_type: str | None = None
    body: str
    url: str
    created_at: datetime | None = None
    updated_at: datetime | None = None


class RenderedPrompt(BaseModel):
    model_config = ConfigDict(frozen=True)

    prompt_markdown: str
    comment_body: str
    has_actionable_items: bool
    should_publish_comment: bool


class PublicationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    comment_id: int | None = None
    comment_url: str | None = None
    comment_written: bool = False
