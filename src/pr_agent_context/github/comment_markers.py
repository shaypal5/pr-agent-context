from __future__ import annotations

from pr_agent_context.constants import MANAGED_COMMENT_MARKER_PREFIX, MANAGED_COMMENT_SCHEMA_VERSION
from pr_agent_context.domain.models import ManagedCommentIdentity


def format_managed_comment_marker(identity: ManagedCommentIdentity) -> str:
    return (
        f"{MANAGED_COMMENT_MARKER_PREFIX}; "
        f"schema={identity.schema_version}; "
        f"pr={identity.pull_request_number}; "
        f"run_id={identity.run_id}; "
        f"run_attempt={identity.run_attempt}; "
        f"head_sha={identity.head_sha}; "
        f"tool_ref={identity.tool_ref} -->"
    )


def parse_managed_comment_marker(body: str) -> ManagedCommentIdentity | None:
    first_line = body.splitlines()[0].strip() if body else ""
    if not first_line.startswith(MANAGED_COMMENT_MARKER_PREFIX):
        return None
    if first_line == f"{MANAGED_COMMENT_MARKER_PREFIX} -->":
        return None
    if not first_line.endswith("-->"):
        return None

    marker_payload = (
        first_line.removeprefix(MANAGED_COMMENT_MARKER_PREFIX).removesuffix("-->").strip()
    )
    if marker_payload.startswith(";"):
        marker_payload = marker_payload[1:].strip()
    if not marker_payload:
        return None

    fields: dict[str, str] = {}
    for entry in marker_payload.split(";"):
        item = entry.strip()
        if not item or "=" not in item:
            return None
        key, value = item.split("=", maxsplit=1)
        fields[key.strip()] = value.strip()

    if fields.get("schema") != MANAGED_COMMENT_SCHEMA_VERSION:
        return None

    required = {"pr", "run_id", "run_attempt", "head_sha", "tool_ref"}
    if not required.issubset(fields):
        return None

    try:
        return ManagedCommentIdentity(
            schema_version=fields["schema"],
            pull_request_number=int(fields["pr"]),
            run_id=int(fields["run_id"]),
            run_attempt=int(fields["run_attempt"]),
            head_sha=fields["head_sha"],
            tool_ref=fields["tool_ref"],
        )
    except ValueError:
        return None
