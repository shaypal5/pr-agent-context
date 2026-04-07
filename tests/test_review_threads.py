from __future__ import annotations

from pr_agent_context.config import CopilotAuthorMatcherConfig
from pr_agent_context.domain.models import ReviewThread
from pr_agent_context.github.review_threads import (
    collect_unresolved_review_threads,
    parse_review_threads,
    wait_for_review_threads_to_settle,
)
from pr_agent_context.prompt.ids import assign_item_ids


def test_parse_review_threads_filters_and_classifies(review_threads_payload):
    nodes = review_threads_payload["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]

    threads = parse_review_threads(
        nodes,
        copilot_matcher=CopilotAuthorMatcherConfig(
            exact_logins=("copilot-pull-request-reviewer[bot]",),
            regex_patterns=("copilot.*bot",),
        ),
    )

    assert [thread.thread_id for thread in threads] == ["PRRT_example_101", "PRRT_example_202"]
    assert [thread.sort_key for thread in threads] == [1001, 2001]
    assert threads[0].classifier == "copilot"
    assert threads[0].messages[0].author_login == "copilot-pull-request-reviewer[bot]"
    assert threads[1].classifier == "review"
    assert threads[1].path == "tests/test_example.py"


def test_parse_review_threads_supports_custom_copilot_matchers():
    nodes = [
        {
            "id": "PRRT_custom_1",
            "isResolved": False,
            "isOutdated": False,
            "path": "src/example.py",
            "line": 10,
            "startLine": None,
            "originalLine": 10,
            "comments": {
                "nodes": [
                    {
                        "databaseId": 99,
                        "body": "body",
                        "createdAt": "2026-03-07T09:00:00Z",
                        "updatedAt": "2026-03-07T09:00:00Z",
                        "url": "https://example.invalid",
                        "author": {"login": "my-custom-bot", "__typename": "Bot"},
                    }
                ]
            },
        }
    ]

    threads = parse_review_threads(
        nodes,
        copilot_matcher=CopilotAuthorMatcherConfig(exact_logins=("my-custom-bot",)),
    )

    assert threads[0].classifier == "copilot"
    assert threads[0].thread_id == "PRRT_custom_1"
    assert threads[0].sort_key == 99


def test_parse_review_threads_matches_copilot_login_without_bot_suffix():
    nodes = [
        {
            "id": "PRRT_copilot_variant_1",
            "isResolved": False,
            "isOutdated": False,
            "path": "src/example.py",
            "line": 10,
            "startLine": None,
            "originalLine": 10,
            "comments": {
                "nodes": [
                    {
                        "databaseId": 101,
                        "body": "body",
                        "createdAt": "2026-03-24T08:52:35Z",
                        "updatedAt": "2026-03-24T08:52:35Z",
                        "url": "https://example.invalid",
                        "author": {
                            "login": "copilot-pull-request-reviewer",
                            "__typename": "Bot",
                        },
                    }
                ]
            },
        }
    ]

    threads = parse_review_threads(
        nodes,
        copilot_matcher=CopilotAuthorMatcherConfig(
            exact_logins=("copilot-pull-request-reviewer[bot]",),
            regex_patterns=("copilot.*bot",),
        ),
    )

    assert threads[0].classifier == "copilot"
    assert threads[0].messages[0].author_login == "copilot-pull-request-reviewer"


def test_assign_item_ids_preserves_numeric_order_for_legacy_int_thread_ids():
    numbered_threads, _, _ = assign_item_ids(
        [
            ReviewThread.model_validate(
                {
                    "thread_id": 10,
                    "classifier": "review",
                    "sort_key": None,
                    "path": "src/example.py",
                    "line": 10,
                    "original_line": 10,
                    "is_resolved": False,
                    "is_outdated": False,
                    "url": "https://example.invalid/thread-10",
                    "messages": [
                        {
                            "comment_id": 10,
                            "author_login": "octocat",
                            "body": "ten",
                            "url": "https://example.invalid/comment-10",
                        }
                    ],
                }
            ),
            ReviewThread.model_validate(
                {
                    "thread_id": 2,
                    "classifier": "review",
                    "sort_key": None,
                    "path": "src/example.py",
                    "line": 2,
                    "original_line": 2,
                    "is_resolved": False,
                    "is_outdated": False,
                    "url": "https://example.invalid/thread-2",
                    "messages": [
                        {
                            "comment_id": 2,
                            "author_login": "octocat",
                            "body": "two",
                            "url": "https://example.invalid/comment-2",
                        }
                    ],
                }
            ),
        ],
        [],
        [],
    )

    assert [thread.thread_id for thread in numbered_threads] == [2, 10]
    assert [thread.item_id for thread in numbered_threads] == ["REVIEW-1", "REVIEW-2"]


class _FakeReviewThreadsClient:
    def __init__(self, payload):
        self.payload = payload

    def graphql(self, query, variables):  # noqa: ARG002
        return self.payload["data"]


def test_wait_for_review_threads_to_settle_collects_once_when_timeout_non_positive(
    review_threads_payload,
):
    threads, debug = wait_for_review_threads_to_settle(
        _FakeReviewThreadsClient(review_threads_payload),
        owner="shaypal5",
        repo="example",
        pull_request_number=17,
        max_threads=50,
        copilot_matcher=CopilotAuthorMatcherConfig(
            exact_logins=("copilot-pull-request-reviewer[bot]",),
            regex_patterns=("copilot.*bot",),
        ),
        timeout_seconds=0,
        poll_interval_seconds=10,
    )

    assert len(threads) == 2
    assert debug["skipped_reason"] == "timeout_non_positive"
    assert debug["poll_count"] == 1
    assert debug["thread_count"] == 2


def test_wait_for_review_threads_to_settle_collects_once_when_poll_interval_non_positive(
    review_threads_payload,
):
    threads, debug = wait_for_review_threads_to_settle(
        _FakeReviewThreadsClient(review_threads_payload),
        owner="shaypal5",
        repo="example",
        pull_request_number=17,
        max_threads=50,
        copilot_matcher=CopilotAuthorMatcherConfig(
            exact_logins=("copilot-pull-request-reviewer[bot]",),
            regex_patterns=("copilot.*bot",),
        ),
        timeout_seconds=10,
        poll_interval_seconds=0,
    )

    assert len(threads) == 2
    assert debug["skipped_reason"] == "poll_interval_non_positive"
    assert debug["poll_count"] == 1
    assert debug["thread_count"] == 2


def test_parse_review_threads_skips_threads_without_messages():
    nodes = [
        {
            "id": "PRRT_empty",
            "isResolved": False,
            "isOutdated": False,
            "path": "src/example.py",
            "line": 10,
            "startLine": None,
            "originalLine": 10,
            "comments": {"nodes": [{"databaseId": None}]},
        }
    ]

    threads = parse_review_threads(
        nodes,
        copilot_matcher=CopilotAuthorMatcherConfig(exact_logins=("copilot",)),
    )

    assert threads == []


class _PagedReviewThreadsClient:
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = 0

    def graphql(self, query, variables):  # noqa: ARG002
        response = self.pages[min(self.calls, len(self.pages) - 1)]
        self.calls += 1
        return response["data"]


def test_collect_unresolved_review_threads_handles_pagination():
    first_page = {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": True, "endCursor": "cursor-1"},
                        "nodes": [
                            {
                                "id": "PRRT_page_2",
                                "isResolved": False,
                                "isOutdated": False,
                                "path": "src/b.py",
                                "line": 20,
                                "startLine": None,
                                "originalLine": 20,
                                "comments": {
                                    "nodes": [
                                        {
                                            "databaseId": 200,
                                            "body": "second",
                                            "createdAt": "2026-03-07T09:00:00Z",
                                            "updatedAt": "2026-03-07T09:00:00Z",
                                            "url": "https://example.invalid/2",
                                            "author": {"login": "octocat", "__typename": "User"},
                                        }
                                    ]
                                },
                            }
                        ],
                    }
                }
            }
        }
    }
    second_page = {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": [
                            {
                                "id": "PRRT_page_1",
                                "isResolved": False,
                                "isOutdated": False,
                                "path": "src/a.py",
                                "line": 10,
                                "startLine": None,
                                "originalLine": 10,
                                "comments": {
                                    "nodes": [
                                        {
                                            "databaseId": 100,
                                            "body": "first",
                                            "createdAt": "2026-03-07T08:00:00Z",
                                            "updatedAt": "2026-03-07T08:00:00Z",
                                            "url": "https://example.invalid/1",
                                            "author": {
                                                "login": "copilot-pull-request-reviewer[bot]",
                                                "__typename": "Bot",
                                            },
                                        }
                                    ]
                                },
                            }
                        ],
                    }
                }
            }
        }
    }
    client = _PagedReviewThreadsClient([first_page, second_page])

    threads = collect_unresolved_review_threads(
        client,
        owner="shaypal5",
        repo="example",
        pull_request_number=17,
        max_threads=50,
        copilot_matcher=CopilotAuthorMatcherConfig(
            exact_logins=("copilot-pull-request-reviewer[bot]",),
            regex_patterns=("copilot.*bot",),
        ),
    )

    assert client.calls == 2
    assert [thread.thread_id for thread in threads] == ["PRRT_page_1", "PRRT_page_2"]


def test_wait_for_review_threads_to_settle_reaches_stable_snapshot(monkeypatch):
    first = {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": [
                            {
                                "id": "PRRT_1",
                                "isResolved": False,
                                "isOutdated": False,
                                "path": "src/example.py",
                                "line": 10,
                                "startLine": None,
                                "originalLine": 10,
                                "comments": {
                                    "nodes": [
                                        {
                                            "databaseId": 1,
                                            "body": "body",
                                            "createdAt": "2026-03-07T08:00:00Z",
                                            "updatedAt": "2026-03-07T08:00:00Z",
                                            "url": "https://example.invalid/1",
                                            "author": {"login": "octocat", "__typename": "User"},
                                        }
                                    ]
                                },
                            }
                        ],
                    }
                }
            }
        }
    }
    client = _PagedReviewThreadsClient([first, first])
    elapsed = iter([0.0, 0.5, 1.0])
    sleeps: list[float] = []
    monkeypatch.setattr("pr_agent_context.github.review_threads._monotonic", lambda: next(elapsed))
    monkeypatch.setattr("pr_agent_context.github.review_threads._sleep", sleeps.append)

    threads, debug = wait_for_review_threads_to_settle(
        client,
        owner="shaypal5",
        repo="example",
        pull_request_number=17,
        max_threads=50,
        copilot_matcher=CopilotAuthorMatcherConfig(exact_logins=("copilot",)),
        timeout_seconds=10,
        poll_interval_seconds=3,
    )

    assert len(threads) == 1
    assert debug["settled"] is True
    assert debug["timed_out"] is False
    assert debug["poll_count"] == 2
    assert sleeps == [3]


def test_wait_for_review_threads_to_settle_times_out(monkeypatch):
    payload = {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": [
                            {
                                "id": "PRRT_1",
                                "isResolved": False,
                                "isOutdated": False,
                                "path": "src/example.py",
                                "line": 10,
                                "startLine": None,
                                "originalLine": 10,
                                "comments": {
                                    "nodes": [
                                        {
                                            "databaseId": 1,
                                            "body": "first",
                                            "createdAt": "2026-03-07T08:00:00Z",
                                            "updatedAt": "2026-03-07T08:00:00Z",
                                            "url": "https://example.invalid/1",
                                            "author": {"login": "octocat", "__typename": "User"},
                                        }
                                    ]
                                },
                            }
                        ],
                    }
                }
            }
        }
    }
    changed_payload = {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": [
                            {
                                "id": "PRRT_2",
                                "isResolved": False,
                                "isOutdated": False,
                                "path": "src/example.py",
                                "line": 20,
                                "startLine": None,
                                "originalLine": 20,
                                "comments": {
                                    "nodes": [
                                        {
                                            "databaseId": 2,
                                            "body": "second",
                                            "createdAt": "2026-03-07T08:00:01Z",
                                            "updatedAt": "2026-03-07T08:00:01Z",
                                            "url": "https://example.invalid/2",
                                            "author": {"login": "octocat", "__typename": "User"},
                                        }
                                    ]
                                },
                            }
                        ],
                    }
                }
            }
        }
    }
    client = _PagedReviewThreadsClient([payload, changed_payload])
    elapsed = iter([0.0, 0.2, 1.0, 1.0])
    sleeps: list[float] = []
    monkeypatch.setattr("pr_agent_context.github.review_threads._monotonic", lambda: next(elapsed))
    monkeypatch.setattr("pr_agent_context.github.review_threads._sleep", sleeps.append)

    threads, debug = wait_for_review_threads_to_settle(
        client,
        owner="shaypal5",
        repo="example",
        pull_request_number=17,
        max_threads=50,
        copilot_matcher=CopilotAuthorMatcherConfig(exact_logins=("copilot",)),
        timeout_seconds=1,
        poll_interval_seconds=3,
    )

    assert [thread.thread_id for thread in threads] == ["PRRT_2"]
    assert debug["settled"] is False
    assert debug["timed_out"] is True
    assert sleeps == [0.8]


def test_collect_unresolved_review_threads_returns_empty_when_max_threads_is_zero():
    client = _PagedReviewThreadsClient([])

    threads = collect_unresolved_review_threads(
        client,
        owner="shaypal5",
        repo="example",
        pull_request_number=17,
        max_threads=0,
        copilot_matcher=CopilotAuthorMatcherConfig(exact_logins=("copilot",)),
    )

    assert threads == []
    assert client.calls == 0
