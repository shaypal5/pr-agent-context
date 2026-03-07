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


class PublicationResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    comment_id: int | None = None
    comment_url: str | None = None
    comment_written: bool = False
