from __future__ import annotations

import pytest

from pr_agent_context.config import TriggerContext
from pr_agent_context.github.api import GitHubApiError
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


class ClosedPullRequestContextClient:
    def request_json(self, method: str, path: str, params=None, payload=None, extra_headers=None):
        assert method == "GET"
        if path.endswith("/commits/merge123/pulls"):
            return [
                {
                    "number": 12,
                    "state": "closed",
                    "updated_at": "2026-03-11T08:00:00Z",
                }
            ]
        if path.endswith("/pulls/12"):
            return {
                "base": {"sha": "base123"},
                "head": {"sha": "feature123"},
            }
        raise AssertionError(f"Unexpected request: {path}")


def test_resolve_pull_request_ref_uses_trigger_sha_for_closed_status_event():
    pull_request, debug = resolve_pull_request_ref(
        ClosedPullRequestContextClient(),
        owner="shaypal5",
        repo="pr-agent-context",
        trigger=TriggerContext(
            event_name="status",
            action=None,
            source="status",
            head_sha="merge123",
        ),
    )

    assert pull_request.number == 12
    assert pull_request.base_sha == "base123"
    assert pull_request.head_sha == "merge123"
    assert debug["resolution"] == "head_sha_lookup"


class _NumberLookupClient:
    def request_json(self, method: str, path: str, params=None, payload=None, extra_headers=None):
        assert method == "GET"
        if path.endswith("/pulls/17"):
            return {"base": {"sha": "base123"}, "head": {"sha": "head123"}}
        raise AssertionError(path)


def test_resolve_pull_request_ref_uses_pull_request_hint():
    from pr_agent_context.config import PullRequestRef

    hint = PullRequestRef(
        owner="shaypal5",
        repo="pr-agent-context",
        number=17,
        base_sha="base123",
        head_sha="head123",
    )

    pull_request, debug = resolve_pull_request_ref(
        _NumberLookupClient(),
        owner="shaypal5",
        repo="pr-agent-context",
        trigger=TriggerContext(event_name="pull_request", action=None, source="pull_request"),
        pull_request_hint=hint,
    )

    assert pull_request == hint
    assert debug["resolution"] == "env_payload_complete"


def test_resolve_pull_request_ref_uses_trigger_pull_request_number():
    pull_request, debug = resolve_pull_request_ref(
        _NumberLookupClient(),
        owner="shaypal5",
        repo="pr-agent-context",
        trigger=TriggerContext(
            event_name="pull_request_review",
            action="submitted",
            source="pull_request_review:submitted",
            pull_request_number=17,
        ),
    )

    assert pull_request.number == 17
    assert pull_request.base_sha == "base123"
    assert debug["resolution"] == "pull_request_number_lookup"


def test_resolve_pull_request_ref_rejects_unresolvable_event():
    with pytest.raises(ValueError, match="Unable to resolve pull request context"):
        resolve_pull_request_ref(
            _NumberLookupClient(),
            owner="shaypal5",
            repo="pr-agent-context",
            trigger=TriggerContext(event_name="status", action=None, source="status"),
        )


def test_resolve_pull_request_ref_errors_when_no_pull_request_found_for_head_sha():
    class NoPullClient:
        def request_json(
            self, method: str, path: str, params=None, payload=None, extra_headers=None
        ):
            if path.endswith("/commits/deadbeef/pulls"):
                return []
            raise AssertionError(path)

    with pytest.raises(ValueError, match="No pull request found for head SHA deadbeef"):
        resolve_pull_request_ref(
            NoPullClient(),
            owner="shaypal5",
            repo="pr-agent-context",
            trigger=TriggerContext(
                event_name="status",
                action=None,
                source="status",
                head_sha="deadbeef",
            ),
        )


def test_resolve_pull_request_ref_errors_when_pull_request_missing_shas():
    class MissingShaClient:
        def request_json(
            self, method: str, path: str, params=None, payload=None, extra_headers=None
        ):
            if path.endswith("/pulls/17"):
                return {"base": {}, "head": {"sha": "head123"}}
            raise AssertionError(path)

    with pytest.raises(ValueError, match="missing base/head SHAs"):
        resolve_pull_request_ref(
            MissingShaClient(),
            owner="shaypal5",
            repo="pr-agent-context",
            trigger=TriggerContext(
                event_name="pull_request_review",
                action="submitted",
                source="pull_request_review:submitted",
                pull_request_number=17,
            ),
        )


def test_resolve_pull_request_ref_wraps_pull_request_fetch_api_error():
    class ErrorClient:
        def request_json(
            self, method: str, path: str, params=None, payload=None, extra_headers=None
        ):
            if path.endswith("/commits/deadbeef/pulls"):
                return [{"number": 17, "state": "open", "updated_at": "2026-03-10T10:00:00Z"}]
            if path.endswith("/pulls/17"):
                raise GitHubApiError(404, "Not Found", "")
            raise AssertionError(path)

    with pytest.raises(
        ValueError, match="Unable to fetch pull request details for head SHA deadbeef"
    ):
        resolve_pull_request_ref(
            ErrorClient(),
            owner="shaypal5",
            repo="pr-agent-context",
            trigger=TriggerContext(
                event_name="status",
                action=None,
                source="status",
                head_sha="deadbeef",
            ),
        )
