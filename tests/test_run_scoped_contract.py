from __future__ import annotations

from pr_agent_context.constants import (
    DEFAULT_ALL_CLEAR_PROMPT,
    DEFAULT_PROMPT_OPENING,
    DEFAULT_PROMPT_TEMPLATE,
    MANAGED_COMMENT_MARKER_PREFIX,
    MANAGED_COMMENT_SCHEMA_VERSION,
)
from pr_agent_context.domain.models import (
    ManagedComment,
    ManagedCommentIdentity,
    PublicationResult,
)


def test_run_scoped_constants_expose_v3_marker_contract():
    assert MANAGED_COMMENT_MARKER_PREFIX == "<!-- pr-agent-context:managed-comment"
    assert MANAGED_COMMENT_SCHEMA_VERSION == "v3"
    assert "{run_id}" in DEFAULT_PROMPT_OPENING
    assert "{run_attempt}" in DEFAULT_PROMPT_OPENING
    assert "{tool_ref}" in DEFAULT_PROMPT_OPENING
    assert "{tool_version}" in DEFAULT_PROMPT_OPENING
    assert "{run_id}" in DEFAULT_ALL_CLEAR_PROMPT
    assert "{{ failing_checks_section }}" in DEFAULT_PROMPT_TEMPLATE


def test_run_scoped_models_preserve_marker_and_publication_metadata():
    identity = ManagedCommentIdentity(
        pull_request_number=17,
        run_id=123,
        run_attempt=4,
        head_sha="deadbeef",
        tool_ref="v3",
    )
    comment = ManagedComment(
        comment_id=9,
        author_login="github-actions[bot]",
        author_type="Bot",
        body="body",
        url="https://example.invalid/comment/9",
        marker=identity,
    )
    publication = PublicationResult(
        comment_id=9,
        comment_url=comment.url,
        comment_written=True,
        action="updated_same_run",
        managed_comment_count=3,
        body_changed=True,
        run_id=identity.run_id,
        run_attempt=identity.run_attempt,
        head_sha=identity.head_sha,
        matched_existing_comment=True,
        matched_comment_run_id=identity.run_id,
        matched_comment_run_attempt=identity.run_attempt,
        sync_debug={"matching_comment_ids": [4, 9], "duplicate_match_count": 1},
    )

    assert comment.marker == identity
    assert publication.model_dump(mode="json")["action"] == "updated_same_run"
    assert publication.sync_debug["duplicate_match_count"] == 1
    assert publication.matched_comment_run_id == 123
