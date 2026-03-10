from __future__ import annotations

from pydantic import ValidationError

from pr_agent_context.constants import MANAGED_COMMENT_MARKER_PREFIX, MANAGED_COMMENT_SCHEMA_VERSION
from pr_agent_context.domain.models import ManagedCommentIdentity


def format_managed_comment_marker(identity: ManagedCommentIdentity) -> str:
    parts = [
        f"schema={identity.schema_version}",
        f"publish_mode={identity.publish_mode}",
        f"pr={identity.pull_request_number}",
        f"head_sha={identity.head_sha}",
        f"trigger_event={identity.trigger_event_name}",
        f"generated_at={identity.generated_at}",
        f"tool_ref={identity.tool_ref}",
    ]
    if identity.run_id is not None:
        parts.append(f"run_id={identity.run_id}")
    if identity.run_attempt is not None:
        parts.append(f"run_attempt={identity.run_attempt}")
    return f"{MANAGED_COMMENT_MARKER_PREFIX}; " + "; ".join(parts) + " -->"


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

    required = {"pr", "publish_mode", "head_sha", "trigger_event", "generated_at", "tool_ref"}
    if not required.issubset(fields):
        return None

    try:
        return ManagedCommentIdentity(
            schema_version=fields["schema"],
            pull_request_number=int(fields["pr"]),
            publish_mode=fields["publish_mode"],  # type: ignore[arg-type]
            head_sha=fields["head_sha"],
            trigger_event_name=fields["trigger_event"],
            generated_at=fields["generated_at"],
            tool_ref=fields["tool_ref"],
            run_id=int(fields["run_id"]) if fields.get("run_id") else None,
            run_attempt=int(fields["run_attempt"]) if fields.get("run_attempt") else None,
        )
    except (ValueError, ValidationError):
        return None
