from __future__ import annotations

from collections.abc import Iterable

from pr_agent_context.constants import MANAGED_COMMENT_MARKER
from pr_agent_context.domain.models import ManagedComment, PublicationResult
from pr_agent_context.github.api import GitHubApiClient, GitHubApiError


def list_issue_comments(
    client: GitHubApiClient,
    *,
    owner: str,
    repo: str,
    pull_request_number: int,
) -> list[ManagedComment]:
    comments: list[ManagedComment] = []
    page = 1
    while True:
        payload = client.request_json(
            "GET",
            f"/repos/{owner}/{repo}/issues/{pull_request_number}/comments",
            params={"per_page": 100, "page": page},
        )
        page_comments = [normalize_issue_comment(comment) for comment in payload]
        comments.extend(page_comments)
        if len(payload) < 100:
            break
        page += 1
    return comments


def normalize_issue_comment(raw_comment: dict[str, object]) -> ManagedComment:
    user = raw_comment.get("user") or {}
    return ManagedComment(
        comment_id=int(raw_comment["id"]),
        author_login=str(user.get("login") or "unknown"),
        author_type=str(user.get("type")) if user.get("type") else None,
        body=str(raw_comment.get("body") or ""),
        url=str(raw_comment.get("html_url") or ""),
        created_at=raw_comment.get("created_at"),
        updated_at=raw_comment.get("updated_at"),
    )


def sync_managed_comment(
    client: GitHubApiClient,
    *,
    owner: str,
    repo: str,
    pull_request_number: int,
    body: str | None,
    delete_comment_when_empty: bool,
    skip_comment_on_readonly_token: bool,
) -> PublicationResult:
    comments = list_issue_comments(
        client,
        owner=owner,
        repo=repo,
        pull_request_number=pull_request_number,
    )
    managed_comments = sorted(
        managed_comments_only(comments),
        key=lambda comment: (
            comment.updated_at.isoformat() if comment.updated_at else "",
            comment.created_at.isoformat() if comment.created_at else "",
            comment.comment_id,
        ),
    )
    primary_comment = managed_comments[-1] if managed_comments else None
    duplicate_comments = managed_comments[:-1] if managed_comments else []
    managed_comment_count = len(managed_comments)
    duplicate_count = len(duplicate_comments)

    try:
        if not body:
            if delete_comment_when_empty:
                for comment in managed_comments:
                    client.request_json(
                        "DELETE",
                        f"/repos/{owner}/{repo}/issues/comments/{comment.comment_id}",
                    )
                return PublicationResult(
                    comment_written=False,
                    action="deleted" if managed_comments else "noop_no_comment",
                    existing_managed_comment_count=managed_comment_count,
                    duplicate_managed_comment_count=duplicate_count,
                )
            if primary_comment is not None:
                return PublicationResult(
                    comment_id=primary_comment.comment_id,
                    comment_url=primary_comment.url,
                    comment_written=True,
                    action="preserved_empty",
                    existing_managed_comment_count=managed_comment_count,
                    duplicate_managed_comment_count=duplicate_count,
                )
            return PublicationResult(
                comment_written=False,
                action="noop_no_comment",
                existing_managed_comment_count=managed_comment_count,
                duplicate_managed_comment_count=duplicate_count,
            )

        for duplicate in duplicate_comments:
            client.request_json(
                "DELETE",
                f"/repos/{owner}/{repo}/issues/comments/{duplicate.comment_id}",
            )

        if primary_comment is None:
            created = client.request_json(
                "POST",
                f"/repos/{owner}/{repo}/issues/{pull_request_number}/comments",
                payload={"body": body},
            )
            normalized = normalize_issue_comment(created)
            return PublicationResult(
                comment_id=normalized.comment_id,
                comment_url=normalized.url,
                comment_written=True,
                action="created",
                existing_managed_comment_count=managed_comment_count,
                duplicate_managed_comment_count=duplicate_count,
                body_changed=True,
            )

        if primary_comment.body != body:
            updated = client.request_json(
                "PATCH",
                f"/repos/{owner}/{repo}/issues/comments/{primary_comment.comment_id}",
                payload={"body": body},
            )
            normalized = normalize_issue_comment(updated)
            return PublicationResult(
                comment_id=normalized.comment_id,
                comment_url=normalized.url,
                comment_written=True,
                action="updated",
                existing_managed_comment_count=managed_comment_count,
                duplicate_managed_comment_count=duplicate_count,
                body_changed=True,
            )

        return PublicationResult(
            comment_id=primary_comment.comment_id,
            comment_url=primary_comment.url,
            comment_written=True,
            action="unchanged",
            existing_managed_comment_count=managed_comment_count,
            duplicate_managed_comment_count=duplicate_count,
            body_changed=False,
        )
    except GitHubApiError as error:
        if skip_comment_on_readonly_token and error.status_code == 403:
            return PublicationResult(
                comment_id=primary_comment.comment_id if primary_comment else None,
                comment_url=primary_comment.url if primary_comment else None,
                comment_written=False,
                action="skipped_forbidden",
                existing_managed_comment_count=managed_comment_count,
                duplicate_managed_comment_count=duplicate_count,
                body_changed=primary_comment is None or primary_comment.body != body,
                skipped_reason="comment mutation skipped after GitHub returned 403",
                error_status_code=error.status_code,
            )
        raise


def managed_comments_only(comments: Iterable[ManagedComment]) -> list[ManagedComment]:
    return [comment for comment in comments if is_managed_comment(comment)]


def is_managed_comment(comment: ManagedComment) -> bool:
    return comment.body.startswith(MANAGED_COMMENT_MARKER) and _is_bot_author(
        comment.author_login, comment.author_type
    )


def _is_bot_author(author_login: str, author_type: str | None) -> bool:
    return author_type == "Bot" or author_login.endswith("[bot]")
