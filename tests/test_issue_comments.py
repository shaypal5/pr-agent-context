from __future__ import annotations

from pr_agent_context.github.api import GitHubApiError
from pr_agent_context.github.issue_comments import (
    managed_comments_only,
    normalize_issue_comment,
    sync_managed_comment,
)


class FakeIssueCommentClient:
    def __init__(self, comments):
        self.comments = list(comments)
        self.deleted_ids: list[int] = []
        self.updated_comment_id: int | None = None
        self.updated_body: str | None = None

    def request_json(self, method, path, params=None, payload=None, extra_headers=None):
        if method == "GET" and path.endswith("/comments"):
            return self.comments
        if method == "DELETE":
            comment_id = int(path.rsplit("/", maxsplit=1)[-1])
            self.deleted_ids.append(comment_id)
            self.comments = [comment for comment in self.comments if comment["id"] != comment_id]
            return {}
        if method == "PATCH":
            comment_id = int(path.rsplit("/", maxsplit=1)[-1])
            self.updated_comment_id = comment_id
            self.updated_body = payload["body"]
            for comment in self.comments:
                if comment["id"] == comment_id:
                    comment["body"] = payload["body"]
                    return comment
        if method == "POST":
            created = {
                "id": 10,
                "body": payload["body"],
                "html_url": "https://github.com/shaypal5/example/pull/17#issuecomment-10",
                "created_at": "2026-03-07T08:50:00Z",
                "updated_at": "2026-03-07T08:50:00Z",
                "user": {
                    "login": "github-actions[bot]",
                    "type": "Bot",
                },
            }
            self.comments.append(created)
            return created
        raise AssertionError(f"Unexpected call: {method} {path}")


class ForbiddenIssueCommentClient(FakeIssueCommentClient):
    def __init__(self, comments, *, fail_method: str):
        super().__init__(comments)
        self.fail_method = fail_method

    def request_json(self, method, path, params=None, payload=None, extra_headers=None):
        if method == self.fail_method:
            raise GitHubApiError(403, "Forbidden", "forbidden")
        return super().request_json(
            method, path, params=params, payload=payload, extra_headers=extra_headers
        )


def test_managed_comments_only_filters_marker_and_bot(issue_comments_payload):
    comments = [normalize_issue_comment(comment) for comment in issue_comments_payload]

    managed_ids = [comment.comment_id for comment in managed_comments_only(comments)]

    assert managed_ids == [2, 3]


def test_sync_managed_comment_updates_newest_and_deletes_duplicates(issue_comments_payload):
    client = FakeIssueCommentClient(issue_comments_payload)

    result = sync_managed_comment(
        client,
        owner="shaypal5",
        repo="example",
        pull_request_number=17,
        body="<!-- pr-agent-context:managed-comment -->\n```markdown\nupdated body\n```",
        delete_comment_when_empty=True,
        skip_comment_on_readonly_token=False,
    )

    assert client.deleted_ids == [2]
    assert client.updated_comment_id == 3
    assert client.updated_body is not None
    assert result.comment_id == 3
    assert result.comment_written is True
    assert result.action == "updated"
    assert result.existing_managed_comment_count == 2
    assert result.duplicate_managed_comment_count == 1
    assert result.body_changed is True


def test_sync_managed_comment_deletes_all_when_body_missing(issue_comments_payload):
    client = FakeIssueCommentClient(issue_comments_payload)

    result = sync_managed_comment(
        client,
        owner="shaypal5",
        repo="example",
        pull_request_number=17,
        body=None,
        delete_comment_when_empty=True,
        skip_comment_on_readonly_token=False,
    )

    assert client.deleted_ids == [2, 3]
    assert result.comment_written is False
    assert result.action == "deleted"


def test_sync_managed_comment_preserves_existing_comment_when_delete_disabled(
    issue_comments_payload,
):
    client = FakeIssueCommentClient(issue_comments_payload)

    result = sync_managed_comment(
        client,
        owner="shaypal5",
        repo="example",
        pull_request_number=17,
        body=None,
        delete_comment_when_empty=False,
        skip_comment_on_readonly_token=False,
    )

    assert client.deleted_ids == []
    assert result.comment_id == 3
    assert result.comment_url == "https://github.com/shaypal5/example/pull/17#issuecomment-3"
    assert result.comment_written is True
    assert result.action == "preserved_empty"


def test_sync_managed_comment_skips_forbidden_create(issue_comments_payload):
    client = ForbiddenIssueCommentClient([issue_comments_payload[0]], fail_method="POST")

    result = sync_managed_comment(
        client,
        owner="shaypal5",
        repo="example",
        pull_request_number=17,
        body="<!-- pr-agent-context:managed-comment -->\n```markdown\ncreated body\n```",
        delete_comment_when_empty=True,
        skip_comment_on_readonly_token=True,
    )

    assert result.comment_written is False
    assert result.comment_id is None
    assert result.action == "skipped_forbidden"
    assert result.existing_managed_comment_count == 0
    assert result.duplicate_managed_comment_count == 0
    assert result.body_changed is True
    assert result.skipped_reason == "comment mutation skipped after GitHub returned 403"
    assert result.error_status_code == 403


def test_sync_managed_comment_skips_forbidden_update(issue_comments_payload):
    client = ForbiddenIssueCommentClient(issue_comments_payload, fail_method="PATCH")

    result = sync_managed_comment(
        client,
        owner="shaypal5",
        repo="example",
        pull_request_number=17,
        body="<!-- pr-agent-context:managed-comment -->\n```markdown\nupdated body\n```",
        delete_comment_when_empty=True,
        skip_comment_on_readonly_token=True,
    )

    assert result.comment_written is False
    assert result.comment_id == 3
    assert result.comment_url == "https://github.com/shaypal5/example/pull/17#issuecomment-3"
    assert result.action == "skipped_forbidden"
    assert result.existing_managed_comment_count == 2
    assert result.duplicate_managed_comment_count == 1
    assert result.body_changed is True
    assert result.skipped_reason == "comment mutation skipped after GitHub returned 403"
    assert result.error_status_code == 403
