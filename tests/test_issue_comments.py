from __future__ import annotations

import pytest

from pr_agent_context.github.api import GitHubApiError
from pr_agent_context.github.issue_comments import (
    _matching_run_comments,
    _select_primary_comment,
    _selection_reason,
    _unchanged_action_for_mode,
    _update_action_for_mode,
    is_managed_comment,
    list_issue_comments,
    managed_comments_only,
    normalize_issue_comment,
    sync_managed_comment,
)


class FakeIssueCommentClient:
    def __init__(self, comments):
        self.comments = list(comments)
        self.updated_comment_id: int | None = None
        self.updated_body: str | None = None
        self.deleted_ids: list[int] = []
        self.minimized_comment_node_ids: list[str] = []
        self.minimized_state_by_node_id: dict[str, bool] = {
            comment["node_id"]: bool(comment.get("is_minimized"))
            for comment in self.comments
            if comment.get("node_id")
        }
        self.comment_list_calls = 0

    def request_json(self, method, path, params=None, payload=None, extra_headers=None):
        if method == "GET" and path.endswith("/comments"):
            self.comment_list_calls += 1
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
                "node_id": "IC_kwDOExample10",
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

    def graphql(self, query, variables):
        if "ids" in variables:
            return {
                "nodes": [
                    {
                        "id": node_id,
                        "isMinimized": self.minimized_state_by_node_id.get(node_id, False),
                    }
                    for node_id in variables["ids"]
                ]
            }
        self.minimized_comment_node_ids.append(variables["subjectId"])
        self.minimized_state_by_node_id[variables["subjectId"]] = True
        return {"minimizeComment": {"minimizedComment": {"isMinimized": True}}}


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


class GraphqlFailingIssueCommentClient(FakeIssueCommentClient):
    def __init__(self, comments, *, failing_node_ids: set[str]):
        super().__init__(comments)
        self.failing_node_ids = failing_node_ids

    def graphql(self, query, variables):
        if "ids" in variables:
            return super().graphql(query, variables)
        node_id = variables["subjectId"]
        if node_id in self.failing_node_ids:
            raise GitHubApiError(403, "Forbidden", "forbidden")
        return super().graphql(query, variables)


class ConcurrentAppendIssueCommentClient(FakeIssueCommentClient):
    def request_json(self, method, path, params=None, payload=None, extra_headers=None):
        if method == "POST" and path.endswith("/comments"):
            created = super().request_json(
                method,
                path,
                params=params,
                payload=payload,
                extra_headers=extra_headers,
            )
            concurrent_comment = _managed_comment_payload(
                comment_id=11,
                run_id=101,
                run_attempt=1,
                body_text="concurrent body",
            )
            self.comments.append(concurrent_comment)
            return created
        return super().request_json(
            method, path, params=params, payload=payload, extra_headers=extra_headers
        )


def _managed_comment_payload(
    *,
    comment_id: int,
    publish_mode: str = "append",
    execution_mode: str = "ci",
    run_id: int = 100,
    run_attempt: int = 1,
    body_text: str = "body",
):
    return {
        "id": comment_id,
        "node_id": f"IC_kwDOExample{comment_id}",
        "body": (
            "<!-- pr-agent-context:managed-comment; schema=v5; "
            f"publish_mode={publish_mode}; execution_mode={execution_mode}; pr=17; "
            "head_sha=def456; trigger_event=pull_request; "
            "generated_at=2026-03-07T08:50:00+00:00; "
            f"tool_ref=v4; run_id={run_id}; run_attempt={run_attempt} -->\n"
            f"```markdown\n{body_text}\n```"
        ),
        "html_url": f"https://github.com/shaypal5/example/pull/17#issuecomment-{comment_id}",
        "created_at": "2026-03-07T08:50:00Z",
        "updated_at": "2026-03-07T08:50:00Z",
        "user": {"login": "github-actions[bot]", "type": "Bot"},
    }


def _sync(client, *, body: str | None):
    return sync_managed_comment(
        client,
        owner="shaypal5",
        repo="example",
        pull_request_number=17,
        run_id=100,
        run_attempt=2,
        head_sha="def456",
        tool_ref="v4",
        trigger_event_name="pull_request",
        publish_mode="update_matching",
        generated_at="2026-03-07T08:50:00+00:00",
        body=body,
        delete_comment_when_empty=True,
        skip_comment_on_readonly_token=True,
        hide_previous_managed_comments_on_append=True,
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
            "<!-- pr-agent-context:managed-comment; schema=v4; publish_mode=append; pr=17; "
            "head_sha=def456; trigger_event=pull_request; generated_at=2026-03-07T08:50:00+00:00; "
            "tool_ref=v4; run_id=100; run_attempt=2 -->\n```markdown\nupdated body\n```"
        ),
    )

    assert client.updated_comment_id == 4
    assert client.updated_body is not None
    assert result.comment_id == 4
    assert result.action == "updated_matching"
    assert result.managed_comment_count == 2
    assert result.matched_existing_comment is True
    assert result.matched_comment_run_id == 100
    assert result.matched_comment_run_attempt == 2


def test_sync_managed_comment_updates_same_run_even_if_head_sha_and_tool_ref_differ(
    issue_comments_payload,
):
    client = FakeIssueCommentClient(issue_comments_payload)

    result = sync_managed_comment(
        client,
        owner="shaypal5",
        repo="example",
        pull_request_number=17,
        run_id=100,
        run_attempt=2,
        head_sha="newsha999",
        tool_ref="v4",
        trigger_event_name="pull_request_review",
        publish_mode="update_matching",
        generated_at="2026-03-07T08:50:00+00:00",
        body=(
            "<!-- pr-agent-context:managed-comment; schema=v4; publish_mode=append; pr=17; "
            "head_sha=newsha999; trigger_event=pull_request_review; "
            "generated_at=2026-03-07T08:50:00+00:00; "
            "tool_ref=v4; run_id=100; run_attempt=2 -->\n```markdown\nupdated body\n```"
        ),
        delete_comment_when_empty=True,
        skip_comment_on_readonly_token=False,
    )

    assert client.updated_comment_id == 4
    assert result.action == "updated_matching"
    assert result.matched_existing_comment is True
    assert result.matched_comment_run_id == 100
    assert result.matched_comment_run_attempt == 2


def test_sync_managed_comment_updates_newest_duplicate_for_same_run(
    issue_comments_payload,
):
    duplicate = {
        **issue_comments_payload[3],
        "id": 14,
        "body": (
            "<!-- pr-agent-context:managed-comment; schema=v4; publish_mode=append; pr=17; "
            "head_sha=oldermeta; trigger_event=pull_request; "
            "generated_at=2026-03-07T08:45:00+00:00; "
            "tool_ref=v0; run_id=100; run_attempt=2 -->\n```markdown\nnewer body\n```"
        ),
        "html_url": "https://github.com/shaypal5/example/pull/17#issuecomment-14",
    }
    client = FakeIssueCommentClient([*issue_comments_payload, duplicate])

    result = _sync(
        client,
        body=(
            "<!-- pr-agent-context:managed-comment; schema=v4; publish_mode=append; pr=17; "
            "head_sha=def456; trigger_event=pull_request; generated_at=2026-03-07T08:50:00+00:00; "
            "tool_ref=v4; run_id=100; run_attempt=2 -->\n```markdown\nupdated body\n```"
        ),
    )

    assert client.updated_comment_id == 14
    assert result.action == "updated_matching"
    assert result.sync_debug["matching_comment_ids"] == [4, 14]
    assert result.sync_debug["duplicate_match_count"] == 1


def test_sync_managed_comment_append_creates_new_comment_for_different_run_attempt(
    issue_comments_payload,
):
    client = FakeIssueCommentClient(issue_comments_payload)

    result = sync_managed_comment(
        client,
        owner="shaypal5",
        repo="example",
        pull_request_number=17,
        run_id=100,
        run_attempt=3,
        head_sha="def456",
        tool_ref="v4",
        trigger_event_name="pull_request",
        publish_mode="append",
        generated_at="2026-03-07T08:55:00+00:00",
        body=(
            "<!-- pr-agent-context:managed-comment; schema=v4; publish_mode=append; pr=17; "
            "head_sha=def456; trigger_event=pull_request; generated_at=2026-03-07T08:55:00+00:00; "
            "tool_ref=v4; run_id=100; run_attempt=3 -->\n```markdown\nnew body\n```"
        ),
        delete_comment_when_empty=True,
        skip_comment_on_readonly_token=False,
    )

    assert client.updated_comment_id is None
    assert result.comment_id == 10
    assert result.action == "created"
    assert result.managed_comment_count == 2
    assert result.matched_existing_comment is False
    assert result.publish_mode == "append"
    assert client.comment_list_calls == 2
    assert client.minimized_comment_node_ids == ["IC_kwDOExample3", "IC_kwDOExample4"]
    assert result.sync_debug["hide_enabled"] is True
    assert result.sync_debug["hidden_comment_ids"] == [3, 4]
    assert result.sync_debug["hidden_comment_node_ids"] == [
        "IC_kwDOExample3",
        "IC_kwDOExample4",
    ]
    assert result.sync_debug["hide_skipped_comment_ids"] == []
    assert result.sync_debug["hide_errors"] == []


def test_sync_managed_comment_append_can_opt_out_of_hiding(issue_comments_payload):
    client = FakeIssueCommentClient(issue_comments_payload)

    result = sync_managed_comment(
        client,
        owner="shaypal5",
        repo="example",
        pull_request_number=17,
        run_id=100,
        run_attempt=3,
        head_sha="def456",
        tool_ref="v4",
        trigger_event_name="pull_request",
        publish_mode="append",
        generated_at="2026-03-07T08:55:00+00:00",
        body=(
            "<!-- pr-agent-context:managed-comment; schema=v4; publish_mode=append; pr=17; "
            "head_sha=def456; trigger_event=pull_request; generated_at=2026-03-07T08:55:00+00:00; "
            "tool_ref=v4; run_id=100; run_attempt=3 -->\n```markdown\nnew body\n```"
        ),
        delete_comment_when_empty=True,
        skip_comment_on_readonly_token=False,
        hide_previous_managed_comments_on_append=False,
    )

    assert result.action == "created"
    assert client.comment_list_calls == 1
    assert client.minimized_comment_node_ids == []
    assert result.sync_debug["hide_enabled"] is False
    assert result.sync_debug["hidden_comment_ids"] == []


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
        tool_ref="v4",
        trigger_event_name="pull_request_review_comment",
        publish_mode="append",
        generated_at="2026-03-07T09:00:00+00:00",
        body=(
            "<!-- pr-agent-context:managed-comment; schema=v4; publish_mode=append; pr=17; "
            "head_sha=feedface; trigger_event=pull_request_review_comment; "
            "generated_at=2026-03-07T09:00:00+00:00; "
            "tool_ref=v4; run_id=200; run_attempt=1 -->\n```markdown\nnew body\n```"
        ),
        delete_comment_when_empty=True,
        skip_comment_on_readonly_token=False,
    )

    assert result.action == "created"
    assert result.managed_comment_count == 0
    assert client.updated_comment_id is None


def test_sync_managed_comment_noops_on_empty_body_in_append_mode(
    issue_comments_payload,
):
    client = FakeIssueCommentClient(issue_comments_payload)

    result = sync_managed_comment(
        client,
        owner="shaypal5",
        repo="example",
        pull_request_number=17,
        run_id=100,
        run_attempt=2,
        head_sha="def456",
        tool_ref="v4",
        trigger_event_name="pull_request",
        publish_mode="append",
        generated_at="2026-03-07T09:00:00+00:00",
        body=None,
        delete_comment_when_empty=False,
        skip_comment_on_readonly_token=False,
    )

    assert result.action == "noop_no_comment"
    assert result.comment_written is False
    assert result.matched_existing_comment is False


def test_sync_managed_comment_noops_without_comment_when_body_missing(
    issue_comments_payload,
):
    client = FakeIssueCommentClient([issue_comments_payload[0]])

    result = sync_managed_comment(
        client,
        owner="shaypal5",
        repo="example",
        pull_request_number=17,
        run_id=100,
        run_attempt=2,
        head_sha="def456",
        tool_ref="v4",
        trigger_event_name="pull_request",
        publish_mode="update_matching",
        generated_at="2026-03-07T09:00:00+00:00",
        body=None,
        delete_comment_when_empty=False,
        skip_comment_on_readonly_token=False,
    )

    assert result.action == "noop_no_comment"
    assert result.comment_written is False
    assert result.matched_existing_comment is False


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
        tool_ref="v4",
        trigger_event_name="pull_request",
        publish_mode="append",
        generated_at="2026-03-07T09:00:00+00:00",
        body=(
            "<!-- pr-agent-context:managed-comment; schema=v4; publish_mode=append; pr=17; "
            "head_sha=abc123; trigger_event=pull_request; generated_at=2026-03-07T09:00:00+00:00; "
            "tool_ref=v4; run_id=300; run_attempt=1 -->\n```markdown\ncreated body\n```"
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


def test_sync_managed_comment_append_skips_hiding_comments_without_node_id(issue_comments_payload):
    payload = [dict(issue_comments_payload[2]), dict(issue_comments_payload[3])]
    payload[0].pop("node_id", None)
    client = FakeIssueCommentClient(payload)

    result = sync_managed_comment(
        client,
        owner="shaypal5",
        repo="example",
        pull_request_number=17,
        run_id=100,
        run_attempt=3,
        head_sha="def456",
        tool_ref="v4",
        trigger_event_name="pull_request",
        publish_mode="append",
        generated_at="2026-03-07T08:55:00+00:00",
        body=(
            "<!-- pr-agent-context:managed-comment; schema=v4; publish_mode=append; pr=17; "
            "head_sha=def456; trigger_event=pull_request; generated_at=2026-03-07T08:55:00+00:00; "
            "tool_ref=v4; run_id=100; run_attempt=3 -->\n```markdown\nnew body\n```"
        ),
        delete_comment_when_empty=True,
        skip_comment_on_readonly_token=False,
    )

    assert result.action == "created"
    assert client.comment_list_calls == 2
    assert client.minimized_comment_node_ids == ["IC_kwDOExample4"]
    assert result.sync_debug["hide_skipped_comment_ids"] == [3]
    assert result.sync_debug["hidden_comment_ids"] == [4]


def test_sync_managed_comment_append_never_hides_unmanaged_bot_comments(issue_comments_payload):
    unmanaged_bot = {
        "id": 9,
        "node_id": "IC_kwDOExample9",
        "body": "plain bot note",
        "html_url": "https://github.com/shaypal5/example/pull/17#issuecomment-9",
        "created_at": "2026-03-07T08:15:00Z",
        "updated_at": "2026-03-07T08:15:00Z",
        "user": {"login": "github-actions[bot]", "type": "Bot"},
    }
    client = FakeIssueCommentClient([*issue_comments_payload, unmanaged_bot])

    result = sync_managed_comment(
        client,
        owner="shaypal5",
        repo="example",
        pull_request_number=17,
        run_id=100,
        run_attempt=3,
        head_sha="def456",
        tool_ref="v4",
        trigger_event_name="pull_request",
        publish_mode="append",
        generated_at="2026-03-07T08:55:00+00:00",
        body=(
            "<!-- pr-agent-context:managed-comment; schema=v4; publish_mode=append; pr=17; "
            "head_sha=def456; trigger_event=pull_request; generated_at=2026-03-07T08:55:00+00:00; "
            "tool_ref=v4; run_id=100; run_attempt=3 -->\n```markdown\nnew body\n```"
        ),
        delete_comment_when_empty=True,
        skip_comment_on_readonly_token=False,
    )

    assert result.action == "created"
    assert client.comment_list_calls == 2
    assert client.minimized_comment_node_ids == ["IC_kwDOExample3", "IC_kwDOExample4"]
    assert 9 not in result.sync_debug["hidden_comment_ids"]


def test_sync_managed_comment_skips_forbidden_update(issue_comments_payload):
    client = ForbiddenIssueCommentClient(issue_comments_payload, fail_method="PATCH")

    result = _sync(
        client,
        body=(
            "<!-- pr-agent-context:managed-comment; schema=v4; publish_mode=append; pr=17; "
            "head_sha=def456; trigger_event=pull_request; generated_at=2026-03-07T08:50:00+00:00; "
            "tool_ref=v4; run_id=100; run_attempt=2 -->\n```markdown\nupdated body\n```"
        ),
    )

    assert result.comment_written is False
    assert result.comment_id == 4
    assert result.action == "skipped_forbidden"
    assert result.managed_comment_count == 2
    assert result.matched_existing_comment is True
    assert result.matched_comment_run_id == 100
    assert result.matched_comment_run_attempt == 2


def test_sync_managed_comment_append_records_hide_errors_without_failing(issue_comments_payload):
    client = GraphqlFailingIssueCommentClient(
        issue_comments_payload,
        failing_node_ids={"IC_kwDOExample3"},
    )

    result = sync_managed_comment(
        client,
        owner="shaypal5",
        repo="example",
        pull_request_number=17,
        run_id=100,
        run_attempt=3,
        head_sha="def456",
        tool_ref="v4",
        trigger_event_name="pull_request",
        publish_mode="append",
        generated_at="2026-03-07T08:55:00+00:00",
        body=(
            "<!-- pr-agent-context:managed-comment; schema=v4; publish_mode=append; pr=17; "
            "head_sha=def456; trigger_event=pull_request; generated_at=2026-03-07T08:55:00+00:00; "
            "tool_ref=v4; run_id=100; run_attempt=3 -->\n```markdown\nnew body\n```"
        ),
        delete_comment_when_empty=True,
        skip_comment_on_readonly_token=False,
    )

    assert result.action == "created"
    assert result.comment_written is True
    assert client.comment_list_calls == 2
    assert result.sync_debug["hidden_comment_ids"] == [4]
    assert result.sync_debug["hide_errors"] == [
        {
            "comment_id": 3,
            "node_id": "IC_kwDOExample3",
            "status_code": 403,
            "message": "Forbidden",
        }
    ]


def test_sync_managed_comment_append_skips_already_minimized_comments(issue_comments_payload):
    payload = [dict(issue_comments_payload[2]), dict(issue_comments_payload[3])]
    payload[0]["is_minimized"] = True
    client = FakeIssueCommentClient(payload)

    result = sync_managed_comment(
        client,
        owner="shaypal5",
        repo="example",
        pull_request_number=17,
        run_id=100,
        run_attempt=3,
        head_sha="def456",
        tool_ref="v4",
        trigger_event_name="pull_request",
        publish_mode="append",
        generated_at="2026-03-07T08:55:00+00:00",
        body=(
            "<!-- pr-agent-context:managed-comment; schema=v4; publish_mode=append; pr=17; "
            "head_sha=def456; trigger_event=pull_request; generated_at=2026-03-07T08:55:00+00:00; "
            "tool_ref=v4; run_id=100; run_attempt=3 -->\n```markdown\nnew body\n```"
        ),
        delete_comment_when_empty=True,
        skip_comment_on_readonly_token=False,
    )

    assert result.action == "created"
    assert client.minimized_comment_node_ids == ["IC_kwDOExample4"]
    assert result.sync_debug["hide_skipped_comment_ids"] == [3]
    assert result.sync_debug["hidden_comment_ids"] == [4]


def test_sync_managed_comment_append_relists_after_create_to_hide_concurrent_comment(
    issue_comments_payload,
):
    client = ConcurrentAppendIssueCommentClient(issue_comments_payload)

    result = sync_managed_comment(
        client,
        owner="shaypal5",
        repo="example",
        pull_request_number=17,
        run_id=100,
        run_attempt=3,
        head_sha="def456",
        tool_ref="v4",
        trigger_event_name="pull_request",
        publish_mode="append",
        generated_at="2026-03-07T08:55:00+00:00",
        body=(
            "<!-- pr-agent-context:managed-comment; schema=v4; publish_mode=append; pr=17; "
            "head_sha=def456; trigger_event=pull_request; generated_at=2026-03-07T08:55:00+00:00; "
            "tool_ref=v4; run_id=100; run_attempt=3 -->\n```markdown\nnew body\n```"
        ),
        delete_comment_when_empty=True,
        skip_comment_on_readonly_token=False,
    )

    assert result.action == "created"
    assert client.comment_list_calls == 2
    assert client.minimized_comment_node_ids == [
        "IC_kwDOExample3",
        "IC_kwDOExample4",
        "IC_kwDOExample11",
    ]
    assert result.sync_debug["hidden_comment_ids"] == [3, 4, 11]


def test_sync_managed_comment_noops_delete_request_when_no_matching_comment(
    issue_comments_payload,
):
    client = FakeIssueCommentClient([issue_comments_payload[0]])

    result = sync_managed_comment(
        client,
        owner="shaypal5",
        repo="example",
        pull_request_number=17,
        run_id=100,
        run_attempt=2,
        head_sha="def456",
        tool_ref="v4",
        trigger_event_name="pull_request",
        publish_mode="update_matching",
        generated_at="2026-03-07T08:50:00+00:00",
        body=None,
        delete_comment_when_empty=True,
        skip_comment_on_readonly_token=False,
    )

    assert result.action == "noop_no_comment"
    assert client.deleted_ids == []


def test_sync_managed_comment_updates_latest_managed_when_requested(
    issue_comments_payload,
):
    client = FakeIssueCommentClient(issue_comments_payload)

    result = sync_managed_comment(
        client,
        owner="shaypal5",
        repo="example",
        pull_request_number=17,
        run_id=999,
        run_attempt=1,
        head_sha="freshsha",
        tool_ref="v4",
        trigger_event_name="pull_request_review",
        publish_mode="update_latest_managed",
        generated_at="2026-03-07T09:10:00+00:00",
        body=(
            "<!-- pr-agent-context:managed-comment; schema=v4; "
            "publish_mode=update_latest_managed; pr=17; "
            "head_sha=freshsha; trigger_event=pull_request_review; "
            "generated_at=2026-03-07T09:10:00+00:00; "
            "tool_ref=v4; run_id=999; run_attempt=1 -->\n```markdown\nlatest body\n```"
        ),
        delete_comment_when_empty=True,
        skip_comment_on_readonly_token=False,
    )

    assert client.updated_comment_id == 4
    assert client.minimized_comment_node_ids == []
    assert result.action == "updated_latest_managed"
    assert result.matched_existing_comment is True


def test_sync_managed_comment_reports_unchanged_latest_managed_for_empty_body(
    issue_comments_payload,
):
    client = FakeIssueCommentClient(issue_comments_payload)

    result = sync_managed_comment(
        client,
        owner="shaypal5",
        repo="example",
        pull_request_number=17,
        run_id=999,
        run_attempt=1,
        head_sha="freshsha",
        tool_ref="v4",
        trigger_event_name="pull_request_review",
        publish_mode="update_latest_managed",
        generated_at="2026-03-07T09:10:00+00:00",
        body=None,
        delete_comment_when_empty=False,
        skip_comment_on_readonly_token=False,
    )

    assert result.action == "unchanged_latest_managed"
    assert result.comment_written is True
    assert result.matched_existing_comment is True
    assert result.sync_debug["action"] == "unchanged_latest_managed"


def test_sync_managed_comment_updates_latest_scoped_comment_when_requested():
    client = FakeIssueCommentClient(
        [
            _managed_comment_payload(comment_id=30, execution_mode="ci", body_text="ci body"),
            _managed_comment_payload(
                comment_id=31,
                execution_mode="refresh",
                run_id=200,
                body_text="older refresh body",
            ),
            _managed_comment_payload(
                comment_id=32,
                execution_mode="refresh",
                run_id=201,
                body_text="newer refresh body",
            ),
        ]
    )

    result = sync_managed_comment(
        client,
        owner="shaypal5",
        repo="example",
        pull_request_number=17,
        run_id=999,
        run_attempt=1,
        head_sha="def456",
        tool_ref="v4",
        trigger_event_name="pull_request_review",
        execution_mode="refresh",
        publish_mode="update_latest_scoped",
        generated_at="2026-03-07T09:10:00+00:00",
        body=_managed_comment_payload(
            comment_id=99,
            publish_mode="update_latest_scoped",
            execution_mode="refresh",
            run_id=999,
            body_text="latest refresh body",
        )["body"],
        delete_comment_when_empty=True,
        skip_comment_on_readonly_token=False,
    )

    assert client.updated_comment_id == 32
    assert client.minimized_comment_node_ids == []
    assert result.action == "updated_latest_scoped"
    assert result.matched_existing_comment is True
    assert result.sync_debug["matching_scoped_comment_ids"] == [31, 32]
    assert _selection_reason(publish_mode="update_latest_scoped", primary_comment=None) == (
        "no_existing_match"
    )
    assert _update_action_for_mode("update_latest_scoped") == "updated_latest_scoped"
    assert _unchanged_action_for_mode("update_latest_scoped") == "unchanged_latest_scoped"


def test_sync_managed_comment_deletes_only_refresh_scoped_comment_when_empty_body():
    client = FakeIssueCommentClient(
        [
            _managed_comment_payload(comment_id=30, execution_mode="ci", body_text="ci body"),
            _managed_comment_payload(
                comment_id=31,
                execution_mode="refresh",
                run_id=201,
                body_text="refresh body",
            ),
        ]
    )

    result = sync_managed_comment(
        client,
        owner="shaypal5",
        repo="example",
        pull_request_number=17,
        run_id=999,
        run_attempt=1,
        head_sha="def456",
        tool_ref="v4",
        trigger_event_name="check_run",
        execution_mode="refresh",
        publish_mode="update_latest_scoped",
        generated_at="2026-03-07T09:10:00+00:00",
        body=None,
        delete_comment_when_empty=True,
        skip_comment_on_readonly_token=False,
    )

    assert client.deleted_ids == [31]
    assert result.action == "deleted"
    assert client.comments[0]["id"] == 30


def test_sync_managed_comment_scoped_empty_body_noops_when_only_ci_comment_exists():
    client = FakeIssueCommentClient(
        [_managed_comment_payload(comment_id=30, execution_mode="ci", body_text="ci body")]
    )

    result = sync_managed_comment(
        client,
        owner="shaypal5",
        repo="example",
        pull_request_number=17,
        run_id=999,
        run_attempt=1,
        head_sha="def456",
        tool_ref="v4",
        trigger_event_name="check_run",
        execution_mode="refresh",
        publish_mode="update_latest_scoped",
        generated_at="2026-03-07T09:10:00+00:00",
        body=None,
        delete_comment_when_empty=True,
        skip_comment_on_readonly_token=False,
    )

    assert result.action == "noop_no_comment"
    assert client.deleted_ids == []


def test_sync_managed_comment_deletes_matching_comment_when_empty_body_requests_delete(
    issue_comments_payload,
):
    client = FakeIssueCommentClient(issue_comments_payload)

    result = sync_managed_comment(
        client,
        owner="shaypal5",
        repo="example",
        pull_request_number=17,
        run_id=100,
        run_attempt=2,
        head_sha="def456",
        tool_ref="v4",
        trigger_event_name="pull_request",
        publish_mode="update_matching",
        generated_at="2026-03-07T08:50:00+00:00",
        body=None,
        delete_comment_when_empty=True,
        skip_comment_on_readonly_token=False,
    )

    assert client.deleted_ids == [4]
    assert result.action == "deleted"
    assert result.comment_written is False
    assert result.matched_existing_comment is True


def test_sync_managed_comment_uses_supplied_generated_at_in_current_identity(
    issue_comments_payload,
):
    client = FakeIssueCommentClient([issue_comments_payload[0]])

    result = sync_managed_comment(
        client,
        owner="shaypal5",
        repo="example",
        pull_request_number=17,
        run_id=321,
        run_attempt=4,
        head_sha="feedbeef",
        tool_ref="v4",
        trigger_event_name="pull_request_review",
        publish_mode="append",
        generated_at="2026-03-08T10:11:12+00:00",
        body=(
            "<!-- pr-agent-context:managed-comment; schema=v4; publish_mode=append; pr=17; "
            "head_sha=feedbeef; trigger_event=pull_request_review; "
            "generated_at=2026-03-08T10:11:12+00:00; "
            "tool_ref=v4; run_id=321; run_attempt=4 -->\n```markdown\ncreated body\n```"
        ),
        delete_comment_when_empty=True,
        skip_comment_on_readonly_token=False,
    )

    assert result.action == "created"
    assert result.sync_debug["current_identity"]["generated_at"] == "2026-03-08T10:11:12+00:00"


def test_is_managed_comment_requires_bot_author():
    comment = normalize_issue_comment(
        {
            "id": 99,
            "body": (
                "<!-- pr-agent-context:managed-comment; schema=v4; publish_mode=append; pr=17; "
                "head_sha=def456; trigger_event=pull_request; "
                "generated_at=2026-03-08T10:11:12+00:00; tool_ref=v4 -->\n```markdown\nbody\n```"
            ),
            "html_url": "https://github.com/shaypal5/example/pull/17#issuecomment-99",
            "created_at": "2026-03-07T08:00:00Z",
            "updated_at": "2026-03-07T08:00:00Z",
            "user": {"login": "shaypalachy", "type": "User"},
        }
    )

    assert is_managed_comment(comment) is False


class PagingIssueCommentClient(FakeIssueCommentClient):
    def request_json(self, method, path, params=None, payload=None, extra_headers=None):
        if method == "GET" and path.endswith("/comments"):
            page = int((params or {}).get("page", 1))
            if page == 1:
                return self.comments[:100]
            if page == 2:
                return self.comments[100:]
            return []
        return super().request_json(
            method, path, params=params, payload=payload, extra_headers=extra_headers
        )


def test_list_issue_comments_handles_pagination(issue_comments_payload):
    extra_comments = [
        {
            "id": 1000 + index,
            "body": "plain body",
            "html_url": f"https://github.com/shaypal5/example/pull/17#issuecomment-{1000 + index}",
            "created_at": "2026-03-07T08:00:00Z",
            "updated_at": "2026-03-07T08:00:00Z",
            "user": {"login": "octocat", "type": "User"},
        }
        for index in range(105)
    ]
    client = PagingIssueCommentClient(extra_comments)

    comments = list_issue_comments(
        client,
        owner="shaypal5",
        repo="example",
        pull_request_number=17,
    )

    assert len(comments) == 105
    assert comments[0].comment_id == 1000
    assert comments[-1].comment_id == 1104


def test_sync_managed_comment_reports_unchanged_matching_when_body_is_identical(
    issue_comments_payload,
):
    client = FakeIssueCommentClient(issue_comments_payload)

    result = _sync(client, body=issue_comments_payload[3]["body"])

    assert result.action == "unchanged_matching"
    assert result.comment_id == 4
    assert client.updated_comment_id is None


def test_sync_managed_comment_reraises_forbidden_errors_when_skip_disabled(
    issue_comments_payload,
):
    client = ForbiddenIssueCommentClient(issue_comments_payload, fail_method="PATCH")

    with pytest.raises(GitHubApiError, match="Forbidden"):
        sync_managed_comment(
            client,
            owner="shaypal5",
            repo="example",
            pull_request_number=17,
            run_id=100,
            run_attempt=2,
            head_sha="def456",
            tool_ref="v4",
            trigger_event_name="pull_request",
            publish_mode="update_matching",
            generated_at="2026-03-07T08:50:00+00:00",
            body=(
                "<!-- pr-agent-context:managed-comment; schema=v4; publish_mode=append; pr=17; "
                "head_sha=def456; trigger_event=pull_request; "
                "generated_at=2026-03-07T08:50:00+00:00; "
                "tool_ref=v4; run_id=100; run_attempt=2 -->\n```markdown\nupdated body\n```"
            ),
            delete_comment_when_empty=True,
            skip_comment_on_readonly_token=False,
        )


def test_issue_comment_helper_functions_cover_unmatched_and_unknown_paths(
    issue_comments_payload,
):
    comments = [normalize_issue_comment(comment) for comment in issue_comments_payload]
    matches = _matching_run_comments(
        comments,
        comments[-1].marker.model_copy(update={"run_id": 100, "run_attempt": 2}),
    )

    assert [comment.comment_id for comment in matches] == [4]
    assert _selection_reason(publish_mode="mystery", primary_comment=comments[-1]) == "unknown"
    assert _update_action_for_mode("append") == "created"
    assert _unchanged_action_for_mode("append") == "noop_no_comment"


def test_select_primary_comment_rejects_unknown_publish_mode(issue_comments_payload):
    comments = managed_comments_only(
        [normalize_issue_comment(comment) for comment in issue_comments_payload]
    )

    with pytest.raises(ValueError, match="Unsupported publish mode: weird"):
        _select_primary_comment(
            managed_comments=comments,
            matching_run_comments=[],
            matching_scoped_comments=[],
            publish_mode="weird",
        )
