from __future__ import annotations

import pytest

from pr_agent_context.domain.models import ManagedCommentIdentity
from pr_agent_context.github.comment_markers import (
    format_managed_comment_marker,
    parse_managed_comment_marker,
)


def test_format_and_parse_managed_comment_marker_round_trip():
    identity = ManagedCommentIdentity(
        pull_request_number=17,
        publish_mode="append",
        execution_mode="refresh",
        run_id=100,
        run_attempt=2,
        head_sha="def456",
        trigger_event_name="pull_request_review",
        generated_at="2026-03-10T10:00:00+00:00",
        tool_ref="v4",
    )

    marker = format_managed_comment_marker(identity)

    assert marker.startswith("<!-- pr-agent-context:managed-comment; schema=v5;")
    assert parse_managed_comment_marker(marker) == identity


def test_parse_managed_comment_marker_rejects_legacy_marker():
    assert parse_managed_comment_marker("<!-- pr-agent-context:managed-comment -->") is None


def test_parse_managed_comment_marker_rejects_missing_fields():
    body = (
        "<!-- pr-agent-context:managed-comment; schema=v5; publish_mode=append; pr=17; "
        "run_id=100; run_attempt=2; head_sha=def456 -->"
    )

    assert parse_managed_comment_marker(body) is None


def test_parse_managed_comment_marker_rejects_unknown_schema():
    body = (
        "<!-- pr-agent-context:managed-comment; schema=v999; publish_mode=append; pr=17; "
        "run_id=100; run_attempt=2; head_sha=def456; trigger_event=pull_request; "
        "generated_at=2026-03-10T10:00:00+00:00; tool_ref=v4 -->"
    )

    assert parse_managed_comment_marker(body) is None


def test_parse_managed_comment_marker_rejects_invalid_entries():
    body = (
        "<!-- pr-agent-context:managed-comment; schema=v5; publish_mode=append; pr=17; "
        "bad-entry; run_id=100; run_attempt=2; head_sha=def456; "
        "trigger_event=pull_request; generated_at=2026-03-10T10:00:00+00:00; tool_ref=v4 -->"
    )

    assert parse_managed_comment_marker(body) is None


def test_parse_managed_comment_marker_rejects_non_integer_identity_fields():
    body = (
        "<!-- pr-agent-context:managed-comment; schema=v5; publish_mode=append; pr=abc; "
        "run_id=100; run_attempt=two; head_sha=def456; trigger_event=pull_request; "
        "generated_at=2026-03-10T10:00:00+00:00; tool_ref=v4 -->"
    )

    assert parse_managed_comment_marker(body) is None


def test_parse_managed_comment_marker_rejects_invalid_execution_mode():
    body = (
        "<!-- pr-agent-context:managed-comment; schema=v5; publish_mode=append; "
        "execution_mode=later; pr=17; head_sha=def456; trigger_event=pull_request; "
        "generated_at=2026-03-10T10:00:00+00:00; tool_ref=v4 -->"
    )

    assert parse_managed_comment_marker(body) is None


def test_parse_managed_comment_marker_rejects_empty_execution_mode_for_v5():
    body = (
        "<!-- pr-agent-context:managed-comment; schema=v5; publish_mode=append; "
        "execution_mode=; pr=17; head_sha=def456; trigger_event=pull_request; "
        "generated_at=2026-03-10T10:00:00+00:00; tool_ref=v4 -->"
    )

    assert parse_managed_comment_marker(body) is None


def test_format_managed_comment_marker_omits_missing_run_identity():
    identity = ManagedCommentIdentity(
        pull_request_number=17,
        publish_mode="append",
        execution_mode="ci",
        head_sha="def456",
        trigger_event_name="status",
        generated_at="2026-03-10T10:00:00+00:00",
        tool_ref="v4",
        run_id=None,
        run_attempt=None,
    )

    marker = format_managed_comment_marker(identity)

    assert "run_id=" not in marker
    assert "run_attempt=" not in marker


def test_format_managed_comment_marker_rejects_missing_execution_mode_for_v5():
    identity = ManagedCommentIdentity(
        pull_request_number=17,
        publish_mode="append",
        execution_mode=None,
        head_sha="def456",
        trigger_event_name="pull_request",
        generated_at="2026-03-10T10:00:00+00:00",
        tool_ref="v4",
    )

    with pytest.raises(ValueError, match="execution_mode is required"):
        format_managed_comment_marker(identity)


def test_parse_managed_comment_marker_rejects_missing_terminator():
    body = (
        "<!-- pr-agent-context:managed-comment; schema=v5; publish_mode=append; pr=17; "
        "head_sha=def456; trigger_event=pull_request; generated_at=2026-03-10T10:00:00+00:00; "
        "tool_ref=v4"
    )

    assert parse_managed_comment_marker(body) is None


def test_parse_managed_comment_marker_rejects_empty_payload_after_prefix():
    assert parse_managed_comment_marker("<!-- pr-agent-context:managed-comment; -->") is None


def test_parse_managed_comment_marker_accepts_payload_without_leading_semicolon():
    body = (
        "<!-- pr-agent-context:managed-comment schema=v5; publish_mode=append; "
        "execution_mode=ci; pr=17; "
        "head_sha=def456; trigger_event=pull_request; generated_at=2026-03-10T10:00:00+00:00; "
        "tool_ref=v4 -->"
    )

    parsed = parse_managed_comment_marker(body)

    assert parsed is not None
    assert parsed.pull_request_number == 17


def test_parse_managed_comment_marker_accepts_legacy_v4_without_execution_mode():
    body = (
        "<!-- pr-agent-context:managed-comment; schema=v4; publish_mode=append; pr=17; "
        "head_sha=def456; trigger_event=pull_request; generated_at=2026-03-10T10:00:00+00:00; "
        "tool_ref=v4 -->"
    )

    parsed = parse_managed_comment_marker(body)

    assert parsed is not None
    assert parsed.schema_version == "v4"
    assert parsed.execution_mode is None


def test_format_managed_comment_marker_omits_execution_mode_for_v4():
    identity = ManagedCommentIdentity(
        schema_version="v4",
        pull_request_number=17,
        publish_mode="append",
        execution_mode=None,
        head_sha="def456",
        trigger_event_name="pull_request",
        generated_at="2026-03-10T10:00:00+00:00",
        tool_ref="v4",
    )

    marker = format_managed_comment_marker(identity)

    assert "execution_mode=" not in marker
    assert parse_managed_comment_marker(marker) == identity
