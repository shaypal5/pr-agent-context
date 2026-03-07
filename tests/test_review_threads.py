from __future__ import annotations

from pr_agent_context.github.review_threads import parse_review_threads


def test_parse_review_threads_filters_and_classifies(review_threads_payload):
    nodes = review_threads_payload["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]

    threads = parse_review_threads(nodes)

    assert [thread.thread_id for thread in threads] == [101, 202]
    assert threads[0].classifier == "copilot"
    assert threads[0].messages[0].author_login == "copilot-pull-request-reviewer[bot]"
    assert threads[1].classifier == "review"
    assert threads[1].path == "tests/test_example.py"
