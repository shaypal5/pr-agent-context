from __future__ import annotations

from collections.abc import Iterable

from pr_agent_context.constants import COPILOT_AUTHOR_LOGINS, COPILOT_AUTHOR_PATTERNS
from pr_agent_context.domain.models import ReviewMessage, ReviewThread
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
          databaseId
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


def collect_unresolved_review_threads(
    client: GitHubApiClient,
    *,
    owner: str,
    repo: str,
    pull_request_number: int,
    max_threads: int,
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
        threads.extend(parse_review_threads(review_threads["nodes"]))
        page_info = review_threads["pageInfo"]
        if not page_info["hasNextPage"]:
            break
        cursor = page_info["endCursor"]
    return sorted(threads, key=lambda thread: thread.thread_id)[:max_threads]


def parse_review_threads(nodes: Iterable[dict[str, object]]) -> list[ReviewThread]:
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
                thread_id=node["databaseId"],
                classifier=_classify_thread(root_message.author_login),
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


def _classify_thread(author_login: str) -> str:
    if author_login in COPILOT_AUTHOR_LOGINS:
        return "copilot"
    if any(pattern.search(author_login) for pattern in COPILOT_AUTHOR_PATTERNS):
        return "copilot"
    return "review"
