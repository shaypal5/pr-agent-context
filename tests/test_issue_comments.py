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
        self.updated_comment_id: int | None = None
        self.updated_body: str | None = None

    def request_json(self, method, path, params=None, payload=None, extra_headers=None):
        if method == "GET" and path.endswith("/comments"):
            return self.comments
        if method == "DELETE":
            comment_id = int(path.rsplit("/", maxsplit=1)[-1])
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


def _sync(client, *, body: str | None):
    return sync_managed_comment(
        client,
        owner="shaypal5",
        repo="example",
        pull_request_number=17,
        run_id=100,
        run_attempt=2,
        head_sha="def456",
        tool_ref="v3",
        body=body,
        delete_comment_when_empty=True,
        skip_comment_on_readonly_token=True,
    )


def test_managed_comments_only_filters_to_run_scoped_marker(issue_comments_payload):
    comments = [normalize_issue_comment(comment) for comment in issue_comments_payload]

    managed_ids = [comment.comment_id for comment in managed_comments_only(comments)]

    assert managed_ids == [3, 4]


def test_sync_managed_comment_updates_only_exact_same_run(issue_comments_payload):
    client = FakeIssueCommentClient(issue_comments_payload)

    result = _sync(
        client,
        body=(
            "<!-- pr-agent-context:managed-comment; schema=v3; pr=17; run_id=100; "
            "run_attempt=2; head_sha=def456; tool_ref=v3 -->\n```markdown\nupdated body\n```"
        ),
    )

    assert client.updated_comment_id == 4
    assert client.updated_body is not None
    assert result.comment_id == 4
    assert result.action == "updated_same_run"
    assert result.managed_comment_count == 2
    assert result.matched_existing_comment is True
    assert result.matched_comment_run_id == 100
    assert result.matched_comment_run_attempt == 2


def test_sync_managed_comment_creates_new_comment_for_different_run_attempt(issue_comments_payload):
    client = FakeIssueCommentClient(issue_comments_payload)

    result = sync_managed_comment(
        client,
        owner="shaypal5",
        repo="example",
        pull_request_number=17,
        run_id=100,
        run_attempt=3,
        head_sha="def456",
        tool_ref="v3",
        body=(
            "<!-- pr-agent-context:managed-comment; schema=v3; pr=17; run_id=100; "
            "run_attempt=3; head_sha=def456; tool_ref=v3 -->\n```markdown\nnew body\n```"
        ),
        delete_comment_when_empty=True,
        skip_comment_on_readonly_token=False,
    )

    assert client.updated_comment_id is None
    assert result.comment_id == 10
    assert result.action == "created"
    assert result.managed_comment_count == 2
    assert result.matched_existing_comment is False


def test_sync_managed_comment_ignores_legacy_marker_comments(issue_comments_payload):
    client = FakeIssueCommentClient(issue_comments_payload[:2])

    result = sync_managed_comment(
        client,
        owner="shaypal5",
        repo="example",
        pull_request_number=17,
        run_id=200,
        run_attempt=1,
        head_sha="feedface",
        tool_ref="v3",
        body=(
            "<!-- pr-agent-context:managed-comment; schema=v3; pr=17; run_id=200; "
            "run_attempt=1; head_sha=feedface; tool_ref=v3 -->\n```markdown\nnew body\n```"
        ),
        delete_comment_when_empty=True,
        skip_comment_on_readonly_token=False,
    )

    assert result.action == "created"
    assert result.managed_comment_count == 0
    assert client.updated_comment_id is None


def test_sync_managed_comment_preserves_only_same_run_when_body_missing(issue_comments_payload):
    client = FakeIssueCommentClient(issue_comments_payload)

    result = sync_managed_comment(
        client,
        owner="shaypal5",
        repo="example",
        pull_request_number=17,
        run_id=100,
        run_attempt=2,
        head_sha="def456",
        tool_ref="v3",
        body=None,
        delete_comment_when_empty=False,
        skip_comment_on_readonly_token=False,
    )

    assert result.action == "preserved_empty"
    assert result.comment_id == 4
    assert result.matched_existing_comment is True


def test_sync_managed_comment_skips_forbidden_create(issue_comments_payload):
    client = ForbiddenIssueCommentClient([issue_comments_payload[0]], fail_method="POST")

    result = sync_managed_comment(
        client,
        owner="shaypal5",
        repo="example",
        pull_request_number=17,
        run_id=300,
        run_attempt=1,
        head_sha="abc123",
        tool_ref="v3",
        body=(
            "<!-- pr-agent-context:managed-comment; schema=v3; pr=17; run_id=300; "
            "run_attempt=1; head_sha=abc123; tool_ref=v3 -->\n```markdown\ncreated body\n```"
        ),
        delete_comment_when_empty=True,
        skip_comment_on_readonly_token=True,
    )

    assert result.comment_written is False
    assert result.action == "skipped_forbidden"
    assert result.matched_existing_comment is False
    assert result.run_id == 300
    assert result.run_attempt == 1
    assert result.error_status_code == 403


def test_sync_managed_comment_skips_forbidden_update(issue_comments_payload):
    client = ForbiddenIssueCommentClient(issue_comments_payload, fail_method="PATCH")

    result = _sync(
        client,
        body=(
            "<!-- pr-agent-context:managed-comment; schema=v3; pr=17; run_id=100; "
            "run_attempt=2; head_sha=def456; tool_ref=v3 -->\n```markdown\nupdated body\n```"
        ),
    )

    assert result.comment_written is False
    assert result.comment_id == 4
    assert result.action == "skipped_forbidden"
    assert result.managed_comment_count == 2
    assert result.matched_existing_comment is True
    assert result.matched_comment_run_id == 100
    assert result.matched_comment_run_attempt == 2
