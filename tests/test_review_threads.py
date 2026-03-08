from __future__ import annotations

from pr_agent_context.config import CopilotAuthorMatcherConfig
from pr_agent_context.domain.models import ReviewThread
from pr_agent_context.github.review_threads import parse_review_threads
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


def test_assign_item_ids_preserves_numeric_order_for_legacy_int_thread_ids():
    numbered_threads, _ = assign_item_ids(
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
    )

    assert [thread.thread_id for thread in numbered_threads] == [2, 10]
    assert [thread.item_id for thread in numbered_threads] == ["REVIEW-1", "REVIEW-2"]
