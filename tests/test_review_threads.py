from __future__ import annotations

from pr_agent_context.config import CopilotAuthorMatcherConfig
from pr_agent_context.github.review_threads import parse_review_threads


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
