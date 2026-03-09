from __future__ import annotations

from pr_agent_context.domain.models import ManagedCommentIdentity
from pr_agent_context.github.comment_markers import (
    format_managed_comment_marker,
    parse_managed_comment_marker,
)


def test_format_and_parse_managed_comment_marker_round_trip():
    identity = ManagedCommentIdentity(
        pull_request_number=17,
        run_id=100,
        run_attempt=2,
        head_sha="def456",
        tool_ref="v3",
    )

    marker = format_managed_comment_marker(identity)

    assert marker.startswith("<!-- pr-agent-context:managed-comment; schema=v3;")
    assert parse_managed_comment_marker(marker) == identity


def test_parse_managed_comment_marker_rejects_legacy_marker():
    assert (
        parse_managed_comment_marker("<!-- pr-agent-context:managed-comment -->")
        is None
    )


def test_parse_managed_comment_marker_rejects_missing_fields():
    body = (
        "<!-- pr-agent-context:managed-comment; schema=v3; pr=17; run_id=100; "
        "run_attempt=2; head_sha=def456 -->"
    )

    assert parse_managed_comment_marker(body) is None


def test_parse_managed_comment_marker_rejects_unknown_schema():
    body = (
        "<!-- pr-agent-context:managed-comment; schema=v3; pr=17; run_id=100; "
        "run_attempt=2; head_sha=def456; tool_ref=v3 -->"
    )

    assert parse_managed_comment_marker(body) is None


def test_parse_managed_comment_marker_rejects_invalid_entries():
    body = (
        "<!-- pr-agent-context:managed-comment; schema=v3; pr=17; bad-entry; "
        "run_id=100; run_attempt=2; head_sha=def456; tool_ref=v3 -->"
    )

    assert parse_managed_comment_marker(body) is None


def test_parse_managed_comment_marker_rejects_non_integer_identity_fields():
    body = (
        "<!-- pr-agent-context:managed-comment; schema=v3; pr=abc; run_id=100; "
        "run_attempt=two; head_sha=def456; tool_ref=v3 -->"
    )

    assert parse_managed_comment_marker(body) is None
