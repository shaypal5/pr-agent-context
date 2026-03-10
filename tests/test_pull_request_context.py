from __future__ import annotations

from pr_agent_context.config import TriggerContext
from pr_agent_context.github.pull_request_context import resolve_pull_request_ref


class PullRequestContextClient:
    def request_json(self, method: str, path: str, params=None, payload=None, extra_headers=None):
        assert method == "GET"
        if path.endswith("/commits/deadbeef/pulls"):
            return [
                {
                    "number": 10,
                    "state": "open",
                    "updated_at": "2026-03-09T08:00:00Z",
                },
                {
                    "number": 11,
                    "state": "open",
                    "updated_at": "2026-03-10T08:00:00Z",
                },
                {
                    "number": 12,
                    "state": "closed",
                    "updated_at": "2026-03-11T08:00:00Z",
                },
            ]
        if path.endswith("/pulls/11"):
            return {
                "base": {"sha": "base123"},
                "head": {"sha": "deadbeef"},
            }
        raise AssertionError(f"Unexpected request: {path}")


def test_resolve_pull_request_ref_prefers_newest_open_pr_for_head_sha():
    pull_request, debug = resolve_pull_request_ref(
        PullRequestContextClient(),
        owner="shaypal5",
        repo="pr-agent-context",
        trigger=TriggerContext(
            event_name="status",
            action=None,
            source="status",
            head_sha="deadbeef",
        ),
    )

    assert pull_request.number == 11
    assert pull_request.base_sha == "base123"
    assert pull_request.head_sha == "deadbeef"
    assert debug["resolution"] == "head_sha_lookup"
