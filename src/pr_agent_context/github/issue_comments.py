from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from pr_agent_context.domain.models import ManagedComment, ManagedCommentIdentity, PublicationResult
from pr_agent_context.github.api import GitHubApiClient, GitHubApiError
from pr_agent_context.github.comment_markers import parse_managed_comment_marker

MINIMIZE_COMMENT_MUTATION = """
mutation MinimizeComment($subjectId: ID!, $classifier: ReportedContentClassifiers!) {
  minimizeComment(input: {subjectId: $subjectId, classifier: $classifier}) {
    minimizedComment {
      isMinimized
    }
  }
}
"""


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
        node_id=str(raw_comment.get("node_id") or "") or None,
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
    run_id: int | None,
    run_attempt: int | None,
    head_sha: str,
    tool_ref: str,
    trigger_event_name: str,
    execution_mode: str = "ci",
    publish_mode: str,
    generated_at: str | None,
    body: str | None,
    delete_comment_when_empty: bool,
    skip_comment_on_readonly_token: bool,
    hide_previous_managed_comments_on_append: bool = True,
) -> PublicationResult:
    generated_at = generated_at or datetime.now(timezone.utc).isoformat()
    current_identity = ManagedCommentIdentity(
        pull_request_number=pull_request_number,
        publish_mode=publish_mode,  # type: ignore[arg-type]
        execution_mode=execution_mode,  # type: ignore[arg-type]
        head_sha=head_sha,
        trigger_event_name=trigger_event_name,
        generated_at=generated_at,
        tool_ref=tool_ref,
        run_id=run_id,
        run_attempt=run_attempt,
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
    matching_run_comments = _matching_run_comments(managed_comments, current_identity)
    matching_scoped_comments = _matching_scoped_comments(managed_comments, current_identity)
    latest_managed_comment = managed_comments[-1] if managed_comments else None
    primary_comment = _select_primary_comment(
        managed_comments=managed_comments,
        matching_run_comments=matching_run_comments,
        matching_scoped_comments=matching_scoped_comments,
        publish_mode=publish_mode,
    )
    managed_comment_count = len(managed_comments)
    sync_debug = {
        "current_identity": current_identity.model_dump(mode="json"),
        "publish_mode": publish_mode,
        "managed_comments": [
            {
                "comment_id": comment.comment_id,
                "url": comment.url,
                "marker": comment.marker.model_dump(mode="json") if comment.marker else None,
            }
            for comment in managed_comments
        ],
        "latest_managed_comment_id": (
            latest_managed_comment.comment_id if latest_managed_comment else None
        ),
        "matching_comment_ids": [comment.comment_id for comment in matching_run_comments],
        "matching_scoped_comment_ids": [comment.comment_id for comment in matching_scoped_comments],
        "duplicate_match_count": max(len(matching_run_comments) - 1, 0),
        "matched_comment_id": primary_comment.comment_id if primary_comment else None,
        "matched_existing_comment": primary_comment is not None,
        "selection_reason": _selection_reason(
            publish_mode=publish_mode,
            primary_comment=primary_comment,
        ),
        "hide_enabled": publish_mode == "append" and hide_previous_managed_comments_on_append,
        "hide_classifier": "OUTDATED",
        "hidden_comment_ids": [],
        "hidden_comment_node_ids": [],
        "hide_skipped_comment_ids": [],
        "hide_errors": [],
    }

    try:
        if not body:
            if publish_mode != "append" and delete_comment_when_empty:
                if primary_comment is not None:
                    client.request_json(
                        "DELETE",
                        f"/repos/{owner}/{repo}/issues/comments/{primary_comment.comment_id}",
                    )
                return PublicationResult(
                    comment_written=False,
                    action="deleted" if primary_comment else "noop_no_comment",
                    managed_comment_count=managed_comment_count,
                    publish_mode=publish_mode,  # type: ignore[arg-type]
                    run_id=run_id,
                    run_attempt=run_attempt,
                    head_sha=head_sha,
                    trigger_event_name=trigger_event_name,
                    matched_existing_comment=primary_comment is not None,
                    matched_comment_id=primary_comment.comment_id if primary_comment else None,
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
            if publish_mode != "append" and primary_comment is not None:
                unchanged_action = _unchanged_action_for_mode(publish_mode)
                return PublicationResult(
                    comment_id=primary_comment.comment_id,
                    comment_url=primary_comment.url,
                    comment_written=True,
                    action=unchanged_action,
                    managed_comment_count=managed_comment_count,
                    publish_mode=publish_mode,  # type: ignore[arg-type]
                    run_id=run_id,
                    run_attempt=run_attempt,
                    head_sha=head_sha,
                    trigger_event_name=trigger_event_name,
                    matched_existing_comment=True,
                    matched_comment_id=primary_comment.comment_id,
                    matched_comment_run_id=primary_comment.marker.run_id
                    if primary_comment and primary_comment.marker
                    else None,
                    matched_comment_run_attempt=primary_comment.marker.run_attempt
                    if primary_comment and primary_comment.marker
                    else None,
                    sync_debug={**sync_debug, "action": unchanged_action},
                )
            return PublicationResult(
                comment_written=False,
                action="noop_no_comment",
                managed_comment_count=managed_comment_count,
                publish_mode=publish_mode,  # type: ignore[arg-type]
                run_id=run_id,
                run_attempt=run_attempt,
                head_sha=head_sha,
                trigger_event_name=trigger_event_name,
                matched_existing_comment=False,
                sync_debug={**sync_debug, "action": "noop_no_comment"},
            )

        if publish_mode == "append" or primary_comment is None:
            created = client.request_json(
                "POST",
                f"/repos/{owner}/{repo}/issues/{pull_request_number}/comments",
                payload={"body": body},
            )
            normalized = normalize_issue_comment(created)
            hide_debug = _hide_previous_managed_comments(
                client,
                managed_comments=managed_comments,
                newly_created_comment=normalized,
                enabled=publish_mode == "append" and hide_previous_managed_comments_on_append,
            )
            return PublicationResult(
                comment_id=normalized.comment_id,
                comment_url=normalized.url,
                comment_written=True,
                action="created",
                managed_comment_count=managed_comment_count,
                body_changed=True,
                publish_mode=publish_mode,  # type: ignore[arg-type]
                run_id=run_id,
                run_attempt=run_attempt,
                head_sha=head_sha,
                trigger_event_name=trigger_event_name,
                matched_existing_comment=(
                    False if publish_mode == "append" else primary_comment is not None
                ),
                sync_debug={
                    **sync_debug,
                    "action": "created",
                    "created_comment_id": normalized.comment_id,
                    **hide_debug,
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
                action=_update_action_for_mode(publish_mode),
                managed_comment_count=managed_comment_count,
                body_changed=True,
                publish_mode=publish_mode,  # type: ignore[arg-type]
                run_id=run_id,
                run_attempt=run_attempt,
                head_sha=head_sha,
                trigger_event_name=trigger_event_name,
                matched_existing_comment=True,
                matched_comment_id=primary_comment.comment_id,
                matched_comment_run_id=primary_comment.marker.run_id
                if primary_comment and primary_comment.marker
                else None,
                matched_comment_run_attempt=primary_comment.marker.run_attempt
                if primary_comment and primary_comment.marker
                else None,
                sync_debug={
                    **sync_debug,
                    "action": _update_action_for_mode(publish_mode),
                    "updated_comment_id": normalized.comment_id,
                },
            )

        return PublicationResult(
            comment_id=primary_comment.comment_id,
            comment_url=primary_comment.url,
            comment_written=True,
            action=_unchanged_action_for_mode(publish_mode),
            managed_comment_count=managed_comment_count,
            body_changed=False,
            publish_mode=publish_mode,  # type: ignore[arg-type]
            run_id=run_id,
            run_attempt=run_attempt,
            head_sha=head_sha,
            trigger_event_name=trigger_event_name,
            matched_existing_comment=True,
            matched_comment_id=primary_comment.comment_id,
            matched_comment_run_id=primary_comment.marker.run_id
            if primary_comment and primary_comment.marker
            else None,
            matched_comment_run_attempt=primary_comment.marker.run_attempt
            if primary_comment and primary_comment.marker
            else None,
            sync_debug={**sync_debug, "action": _unchanged_action_for_mode(publish_mode)},
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
                publish_mode=publish_mode,  # type: ignore[arg-type]
                run_id=run_id,
                run_attempt=run_attempt,
                head_sha=head_sha,
                trigger_event_name=trigger_event_name,
                matched_existing_comment=primary_comment is not None,
                matched_comment_id=primary_comment.comment_id if primary_comment else None,
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


def _matching_run_comments(
    comments: Iterable[ManagedComment],
    identity: ManagedCommentIdentity,
) -> list[ManagedComment]:
    matches: list[ManagedComment] = []
    for comment in comments:
        if comment.marker is None:
            continue
        if (
            comment.marker.pull_request_number == identity.pull_request_number
            and comment.marker.run_id == identity.run_id
            and comment.marker.run_attempt == identity.run_attempt
        ):
            matches.append(comment)
    return matches


def _select_primary_comment(
    *,
    managed_comments: list[ManagedComment],
    matching_run_comments: list[ManagedComment],
    matching_scoped_comments: list[ManagedComment],
    publish_mode: str,
) -> ManagedComment | None:
    if publish_mode == "append":
        return None
    if publish_mode == "update_latest_managed":
        return managed_comments[-1] if managed_comments else None
    if publish_mode == "update_matching":
        return matching_run_comments[-1] if matching_run_comments else None
    if publish_mode == "update_latest_scoped":
        return matching_scoped_comments[-1] if matching_scoped_comments else None
    raise ValueError(f"Unsupported publish mode: {publish_mode}")


def _selection_reason(*, publish_mode: str, primary_comment: ManagedComment | None) -> str:
    if publish_mode == "append":
        return "append_always_creates"
    if primary_comment is None:
        return "no_existing_match"
    if publish_mode == "update_latest_managed":
        return "selected_latest_managed"
    if publish_mode == "update_matching":
        return "selected_matching_run"
    if publish_mode == "update_latest_scoped":
        return "selected_latest_scoped"
    return "unknown"


def _update_action_for_mode(publish_mode: str) -> str:
    if publish_mode == "update_latest_managed":
        return "updated_latest_managed"
    if publish_mode == "update_matching":
        return "updated_matching"
    if publish_mode == "update_latest_scoped":
        return "updated_latest_scoped"
    return "created"


def _unchanged_action_for_mode(publish_mode: str) -> str:
    if publish_mode == "update_latest_managed":
        return "unchanged_latest_managed"
    if publish_mode == "update_matching":
        return "unchanged_matching"
    if publish_mode == "update_latest_scoped":
        return "unchanged_latest_scoped"
    return "noop_no_comment"


def _matching_scoped_comments(
    comments: Iterable[ManagedComment],
    identity: ManagedCommentIdentity,
) -> list[ManagedComment]:
    matches: list[ManagedComment] = []
    for comment in comments:
        if comment.marker is None or comment.marker.execution_mode is None:
            continue
        if (
            comment.marker.pull_request_number == identity.pull_request_number
            and comment.marker.execution_mode == identity.execution_mode
        ):
            matches.append(comment)
    return matches


def _is_bot_author(author_login: str, author_type: str | None) -> bool:
    return author_type == "Bot" or author_login.endswith("[bot]")


def _hide_previous_managed_comments(
    client: GitHubApiClient,
    *,
    managed_comments: list[ManagedComment],
    newly_created_comment: ManagedComment,
    enabled: bool,
) -> dict[str, Any]:
    debug: dict[str, Any] = {
        "hide_enabled": enabled,
        "hide_classifier": "OUTDATED",
        "hidden_comment_ids": [],
        "hidden_comment_node_ids": [],
        "hide_skipped_comment_ids": [],
        "hide_errors": [],
    }
    if not enabled:
        return debug

    for comment in managed_comments:
        if comment.comment_id == newly_created_comment.comment_id:
            continue
        if not comment.node_id:
            debug["hide_skipped_comment_ids"].append(comment.comment_id)
            continue
        try:
            client.graphql(
                MINIMIZE_COMMENT_MUTATION,
                {
                    "subjectId": comment.node_id,
                    "classifier": "OUTDATED",
                },
            )
            debug["hidden_comment_ids"].append(comment.comment_id)
            debug["hidden_comment_node_ids"].append(comment.node_id)
        except GitHubApiError as error:
            debug["hide_errors"].append(
                {
                    "comment_id": comment.comment_id,
                    "node_id": comment.node_id,
                    "status_code": error.status_code,
                    "message": error.message,
                }
            )
        except Exception as error:  # pragma: no cover - defensive fallback
            debug["hide_errors"].append(
                {
                    "comment_id": comment.comment_id,
                    "node_id": comment.node_id,
                    "status_code": None,
                    "message": str(error),
                }
            )
    return debug
