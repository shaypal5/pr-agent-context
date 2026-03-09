from __future__ import annotations

from collections.abc import Iterable

from pr_agent_context.domain.models import ManagedComment, ManagedCommentIdentity, PublicationResult
from pr_agent_context.github.api import GitHubApiClient, GitHubApiError
from pr_agent_context.github.comment_markers import parse_managed_comment_marker


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
    body = str(raw_comment.get("body") or "")
    return ManagedComment(
        comment_id=int(raw_comment["id"]),
        author_login=str(user.get("login") or "unknown"),
        author_type=str(user.get("type")) if user.get("type") else None,
        body=body,
        url=str(raw_comment.get("html_url") or ""),
        created_at=raw_comment.get("created_at"),
        updated_at=raw_comment.get("updated_at"),
        marker=parse_managed_comment_marker(body),
    )


def sync_managed_comment(
    client: GitHubApiClient,
    *,
    owner: str,
    repo: str,
    pull_request_number: int,
    run_id: int,
    run_attempt: int,
    head_sha: str,
    tool_ref: str,
    body: str | None,
    delete_comment_when_empty: bool,
    skip_comment_on_readonly_token: bool,
) -> PublicationResult:
    current_identity = ManagedCommentIdentity(
        pull_request_number=pull_request_number,
        run_id=run_id,
        run_attempt=run_attempt,
        head_sha=head_sha,
        tool_ref=tool_ref,
    )
    comments = list_issue_comments(
        client,
        owner=owner,
        repo=repo,
        pull_request_number=pull_request_number,
    )
    managed_comments = sorted(
        managed_comments_only(comments), key=lambda comment: comment.comment_id
    )
    primary_comment = _find_matching_run_comment(managed_comments, current_identity)
    managed_comment_count = len(managed_comments)
    sync_debug = {
        "current_identity": current_identity.model_dump(mode="json"),
        "managed_comments": [
            {
                "comment_id": comment.comment_id,
                "url": comment.url,
                "marker": comment.marker.model_dump(mode="json") if comment.marker else None,
            }
            for comment in managed_comments
        ],
        "matched_comment_id": primary_comment.comment_id if primary_comment else None,
        "matched_existing_comment": primary_comment is not None,
    }

    try:
        if not body:
            if delete_comment_when_empty:
                if primary_comment is not None:
                    client.request_json(
                        "DELETE",
                        f"/repos/{owner}/{repo}/issues/comments/{primary_comment.comment_id}",
                    )
                return PublicationResult(
                    comment_written=False,
                    action="deleted" if primary_comment else "noop_no_comment",
                    managed_comment_count=managed_comment_count,
                    run_id=run_id,
                    run_attempt=run_attempt,
                    head_sha=head_sha,
                    matched_existing_comment=primary_comment is not None,
                    matched_comment_run_id=primary_comment.marker.run_id
                    if primary_comment and primary_comment.marker
                    else None,
                    matched_comment_run_attempt=primary_comment.marker.run_attempt
                    if primary_comment and primary_comment.marker
                    else None,
                    sync_debug={
                        **sync_debug,
                        "action": "deleted" if primary_comment else "noop_no_comment",
                    },
                )
            if primary_comment is not None:
                return PublicationResult(
                    comment_id=primary_comment.comment_id,
                    comment_url=primary_comment.url,
                    comment_written=True,
                    action="preserved_empty",
                    managed_comment_count=managed_comment_count,
                    run_id=run_id,
                    run_attempt=run_attempt,
                    head_sha=head_sha,
                    matched_existing_comment=True,
                    matched_comment_run_id=primary_comment.marker.run_id
                    if primary_comment and primary_comment.marker
                    else None,
                    matched_comment_run_attempt=primary_comment.marker.run_attempt
                    if primary_comment and primary_comment.marker
                    else None,
                    sync_debug={**sync_debug, "action": "preserved_empty"},
                )
            return PublicationResult(
                comment_written=False,
                action="noop_no_comment",
                managed_comment_count=managed_comment_count,
                run_id=run_id,
                run_attempt=run_attempt,
                head_sha=head_sha,
                matched_existing_comment=False,
                sync_debug={**sync_debug, "action": "noop_no_comment"},
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
                managed_comment_count=managed_comment_count,
                body_changed=True,
                run_id=run_id,
                run_attempt=run_attempt,
                head_sha=head_sha,
                matched_existing_comment=False,
                sync_debug={
                    **sync_debug,
                    "action": "created",
                    "created_comment_id": normalized.comment_id,
                },
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
                action="updated_same_run",
                managed_comment_count=managed_comment_count,
                body_changed=True,
                run_id=run_id,
                run_attempt=run_attempt,
                head_sha=head_sha,
                matched_existing_comment=True,
                matched_comment_run_id=primary_comment.marker.run_id
                if primary_comment and primary_comment.marker
                else None,
                matched_comment_run_attempt=primary_comment.marker.run_attempt
                if primary_comment and primary_comment.marker
                else None,
                sync_debug={
                    **sync_debug,
                    "action": "updated_same_run",
                    "updated_comment_id": normalized.comment_id,
                },
            )

        return PublicationResult(
            comment_id=primary_comment.comment_id,
            comment_url=primary_comment.url,
            comment_written=True,
            action="unchanged_same_run",
            managed_comment_count=managed_comment_count,
            body_changed=False,
            run_id=run_id,
            run_attempt=run_attempt,
            head_sha=head_sha,
            matched_existing_comment=True,
            matched_comment_run_id=primary_comment.marker.run_id
            if primary_comment and primary_comment.marker
            else None,
            matched_comment_run_attempt=primary_comment.marker.run_attempt
            if primary_comment and primary_comment.marker
            else None,
            sync_debug={**sync_debug, "action": "unchanged_same_run"},
        )
    except GitHubApiError as error:
        if skip_comment_on_readonly_token and error.status_code == 403:
            return PublicationResult(
                comment_id=primary_comment.comment_id if primary_comment else None,
                comment_url=primary_comment.url if primary_comment else None,
                comment_written=False,
                action="skipped_forbidden",
                managed_comment_count=managed_comment_count,
                body_changed=primary_comment is None or primary_comment.body != body,
                skipped_reason="comment mutation skipped after GitHub returned 403",
                error_status_code=error.status_code,
                run_id=run_id,
                run_attempt=run_attempt,
                head_sha=head_sha,
                matched_existing_comment=primary_comment is not None,
                matched_comment_run_id=primary_comment.marker.run_id
                if primary_comment and primary_comment.marker
                else None,
                matched_comment_run_attempt=primary_comment.marker.run_attempt
                if primary_comment and primary_comment.marker
                else None,
                sync_debug={
                    **sync_debug,
                    "action": "skipped_forbidden",
                    "error_status_code": error.status_code,
                },
            )
        raise


def managed_comments_only(comments: Iterable[ManagedComment]) -> list[ManagedComment]:
    return [comment for comment in comments if is_managed_comment(comment)]


def is_managed_comment(comment: ManagedComment) -> bool:
    return comment.marker is not None and _is_bot_author(comment.author_login, comment.author_type)


def _find_matching_run_comment(
    comments: Iterable[ManagedComment],
    identity: ManagedCommentIdentity,
) -> ManagedComment | None:
    for comment in comments:
        if comment.marker == identity:
            return comment
    return None


def _is_bot_author(author_login: str, author_type: str | None) -> bool:
    return author_type == "Bot" or author_login.endswith("[bot]")
