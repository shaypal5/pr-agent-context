from __future__ import annotations

import time
from collections.abc import Iterable

from pr_agent_context.config import CopilotAuthorMatcherConfig
from pr_agent_context.domain.models import ReviewMessage, ReviewThread, review_thread_sort_key
from pr_agent_context.github.api import GitHubApiClient

REVIEW_THREADS_QUERY = """
query PullRequestReviewThreads(
  $owner: String!,
  $repo: String!,
  $pullRequestNumber: Int!,
  $after: String
) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $pullRequestNumber) {
      reviewThreads(first: 50, after: $after) {
        pageInfo {
          hasNextPage
          endCursor
        }
        nodes {
          id
          isResolved
          isOutdated
          path
          line
          startLine
          originalLine
          comments(first: 20) {
            nodes {
              databaseId
              body
              createdAt
              updatedAt
              url
              author {
                login
                __typename
              }
            }
          }
        }
      }
    }
  }
}
"""

_monotonic = time.monotonic
_sleep = time.sleep


def collect_unresolved_review_threads(
    client: GitHubApiClient,
    *,
    owner: str,
    repo: str,
    pull_request_number: int,
    max_threads: int,
    copilot_matcher: CopilotAuthorMatcherConfig,
) -> list[ReviewThread]:
    threads: list[ReviewThread] = []
    cursor: str | None = None
    while len(threads) < max_threads:
        response = client.graphql(
            REVIEW_THREADS_QUERY,
            {
                "owner": owner,
                "repo": repo,
                "pullRequestNumber": pull_request_number,
                "after": cursor,
            },
        )
        pull_request = response["repository"]["pullRequest"]
        review_threads = pull_request["reviewThreads"]
        threads.extend(
            parse_review_threads(review_threads["nodes"], copilot_matcher=copilot_matcher)
        )
        page_info = review_threads["pageInfo"]
        if not page_info["hasNextPage"]:
            break
        cursor = page_info["endCursor"]
    return sorted(threads, key=review_thread_sort_key)[:max_threads]


def wait_for_review_threads_to_settle(
    client: GitHubApiClient,
    *,
    owner: str,
    repo: str,
    pull_request_number: int,
    max_threads: int,
    copilot_matcher: CopilotAuthorMatcherConfig,
    timeout_seconds: int,
    poll_interval_seconds: int,
) -> tuple[list[ReviewThread], dict[str, object]]:
    if timeout_seconds <= 0:
        threads = collect_unresolved_review_threads(
            client,
            owner=owner,
            repo=repo,
            pull_request_number=pull_request_number,
            max_threads=max_threads,
            copilot_matcher=copilot_matcher,
        )
        return threads, {
            "enabled": True,
            "settled": False,
            "timed_out": False,
            "skipped_reason": "timeout_non_positive",
            "poll_count": 1,
            "elapsed_seconds": 0.0,
            "thread_count": len(threads),
        }
    if poll_interval_seconds <= 0:
        threads = collect_unresolved_review_threads(
            client,
            owner=owner,
            repo=repo,
            pull_request_number=pull_request_number,
            max_threads=max_threads,
            copilot_matcher=copilot_matcher,
        )
        return threads, {
            "enabled": True,
            "settled": False,
            "timed_out": False,
            "skipped_reason": "poll_interval_non_positive",
            "poll_count": 1,
            "elapsed_seconds": 0.0,
            "thread_count": len(threads),
        }

    start = _monotonic()
    poll_count = 0
    stable_snapshots = 0
    last_fingerprint: tuple[int, ...] | None = None
    latest_threads: list[ReviewThread] = []
    while True:
        poll_count += 1
        latest_threads = collect_unresolved_review_threads(
            client,
            owner=owner,
            repo=repo,
            pull_request_number=pull_request_number,
            max_threads=max_threads,
            copilot_matcher=copilot_matcher,
        )
        fingerprint = tuple(
            sorted(message.comment_id for thread in latest_threads for message in thread.messages)
        )
        if fingerprint == last_fingerprint:
            stable_snapshots += 1
        else:
            stable_snapshots = 0
            last_fingerprint = fingerprint

        elapsed_seconds = max(_monotonic() - start, 0.0)
        settled = stable_snapshots >= 1
        timed_out = elapsed_seconds >= timeout_seconds
        if settled or timed_out:
            return latest_threads, {
                "enabled": True,
                "settled": settled,
                "timed_out": timed_out and not settled,
                "skipped_reason": "",
                "poll_count": poll_count,
                "elapsed_seconds": round(elapsed_seconds, 3),
                "thread_count": len(latest_threads),
            }
        remaining = max(timeout_seconds - elapsed_seconds, 0.0)
        sleep_seconds = min(poll_interval_seconds, remaining)
        _sleep(sleep_seconds)


def parse_review_threads(
    nodes: Iterable[dict[str, object]],
    *,
    copilot_matcher: CopilotAuthorMatcherConfig,
) -> list[ReviewThread]:
    parsed_threads: list[ReviewThread] = []
    for node in nodes:
        if node.get("isResolved") or node.get("isOutdated"):
            continue
        messages = [
            ReviewMessage(
                comment_id=raw_message["databaseId"],
                author_login=(raw_message.get("author") or {}).get("login", "unknown"),
                author_type=(raw_message.get("author") or {}).get("__typename"),
                body=raw_message.get("body", ""),
                created_at=raw_message.get("createdAt"),
                updated_at=raw_message.get("updatedAt"),
                url=raw_message.get("url", ""),
            )
            for raw_message in node["comments"]["nodes"]
            if raw_message.get("databaseId")
        ]
        if not messages:
            continue
        root_message = messages[0]
        parsed_threads.append(
            ReviewThread(
                thread_id=node["id"],
                sort_key=root_message.comment_id,
                classifier="copilot"
                if copilot_matcher.matches(root_message.author_login)
                else "review",
                path=node.get("path"),
                line=node.get("line"),
                start_line=node.get("startLine"),
                original_line=node.get("originalLine"),
                is_resolved=bool(node.get("isResolved")),
                is_outdated=bool(node.get("isOutdated")),
                url=root_message.url,
                messages=messages,
            )
        )
    return parsed_threads
