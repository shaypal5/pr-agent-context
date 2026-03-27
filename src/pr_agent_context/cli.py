from __future__ import annotations

import argparse
import json
import os
import traceback
from collections.abc import Sequence
from datetime import datetime, timezone
from pathlib import Path

from pr_agent_context import __version__
from pr_agent_context.config import (
    RunConfig,
    _resolve_execution_mode,
    load_trigger_context_from_env,
    parse_bool_env,
)
from pr_agent_context.github.api import GitHubApiClient
from pr_agent_context.github.issue_comments import sync_managed_comment
from pr_agent_context.github.pull_request_context import resolve_pull_request_ref
from pr_agent_context.prompt.render import build_managed_comment_body
from pr_agent_context.services.run import run_service


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pr-agent-context")
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("run", help="Collect PR context and manage the PR comment.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "run":
        try:
            config = RunConfig.from_env()
        except Exception as error:  # pragma: no cover - exercised via fallback tests
            _handle_run_failure(error, config=None)
            return 0
        try:
            return run_service(config)
        except Exception as error:  # pragma: no cover - exercised via fallback tests
            _handle_run_failure(error, config=config)
            return 0
    parser.error(f"Unsupported command: {args.command}")
    return 2


def _handle_run_failure(error: Exception, *, config: RunConfig | None) -> None:
    traceback.print_exc()
    env = os.environ
    context = _resolve_failure_context(config=config, env=env)
    payload = {
        "tool": "pr-agent-context",
        "event": "fatal_error",
        "version": __version__,
        "tool_ref": config.tool_ref if config else env.get("PR_AGENT_CONTEXT_TOOL_REF", ""),
        "repository": context["repository"] if context else env.get("GITHUB_REPOSITORY", ""),
        "pull_request_number": context["pull_request_number"] if context else "",
        "head_sha": context["head_sha"] if context else "",
        "run_id": context["run_id"] if context else env.get("GITHUB_RUN_ID", ""),
        "run_attempt": context["run_attempt"] if context else env.get("GITHUB_RUN_ATTEMPT", ""),
        "error_type": type(error).__name__,
        "error_message": str(error),
    }
    print(json.dumps(_filtered_log_payload(payload), sort_keys=True))

    publication = None
    if context:
        try:
            generated_at = datetime.now(timezone.utc).isoformat()
            client = GitHubApiClient(
                token=context["github_token"],
                api_url=context["github_api_url"],
            )
            body = build_managed_comment_body(
                _build_failure_markdown(context=context, error=error),
                pull_request_number=int(context["pull_request_number"]),
                run_id=int(context["run_id"]),
                run_attempt=int(context["run_attempt"]),
                trigger_event_name=str(context.get("trigger_event_name") or "unknown"),
                trigger_label=str(context.get("trigger_label") or "pull request updated"),
                execution_mode=str(context.get("execution_mode") or "ci"),
                publish_mode=str(context.get("publish_mode") or "append"),
                head_sha=str(context["head_sha"]),
                tool_ref=str(context.get("tool_ref") or "unknown"),
                generated_at=generated_at,
            )
            publication = sync_managed_comment(
                client,
                owner=context["owner"],
                repo=context["repo"],
                pull_request_number=context["pull_request_number"],
                run_id=context["run_id"],
                run_attempt=context["run_attempt"],
                head_sha=context["head_sha"],
                tool_ref=context.get("tool_ref") or "unknown",
                trigger_event_name=context.get("trigger_event_name") or "unknown",
                execution_mode=context.get("execution_mode") or "ci",
                publish_mode=context.get("publish_mode") or "append",
                generated_at=generated_at,
                body=body,
                delete_comment_when_empty=False,
                skip_comment_on_readonly_token=context["skip_comment_on_readonly_token"],
                hide_previous_managed_comments_on_append=context[
                    "hide_previous_managed_comments_on_append"
                ],
            )
            print(
                json.dumps(
                    _filtered_log_payload(
                        {
                            "tool": "pr-agent-context",
                            "event": "fatal_error_comment_sync",
                            "action": publication.action,
                            "comment_written": publication.comment_written,
                            "comment_id": publication.comment_id or "",
                            "comment_url": publication.comment_url or "",
                            "skipped_reason": publication.skipped_reason or "",
                            "error_status_code": publication.error_status_code or "",
                        }
                    ),
                    sort_keys=True,
                )
            )
        except Exception as sync_error:  # pragma: no cover - exercised via fallback tests
            print(
                json.dumps(
                    {
                        "tool": "pr-agent-context",
                        "event": "fatal_error_comment_sync_failed",
                        "error_type": type(sync_error).__name__,
                        "error_message": str(sync_error),
                    },
                    sort_keys=True,
                )
            )

    _write_failure_outputs(config=config, env=env, publication=publication)


def _resolve_failure_context(
    *,
    config: RunConfig | None,
    env: dict[str, str],
) -> dict[str, object] | None:
    if config is not None:
        trigger = getattr(config, "trigger", None)
        if config.pull_request is not None:
            return {
                "owner": config.pull_request.owner,
                "repo": config.pull_request.repo,
                "repository": f"{config.pull_request.owner}/{config.pull_request.repo}",
                "pull_request_number": config.pull_request.number,
                "head_sha": config.pull_request.head_sha,
                "run_id": config.run_id,
                "run_attempt": config.run_attempt,
                "tool_ref": config.tool_ref,
                "trigger_event_name": getattr(trigger, "event_name", "unknown"),
                "trigger_label": getattr(trigger, "label", "pull request updated"),
                "execution_mode": getattr(config, "execution_mode", "ci"),
                "publish_mode": getattr(config, "publish_mode", "append"),
                "hide_previous_managed_comments_on_append": getattr(
                    config, "hide_previous_managed_comments_on_append", True
                ),
                "github_token": config.github_token,
                "github_api_url": config.github_api_url,
                "skip_comment_on_readonly_token": config.skip_comment_on_readonly_token,
            }

    repository = env.get("GITHUB_REPOSITORY", "")
    github_token = env.get("GITHUB_TOKEN", "")
    event_path = env.get("GITHUB_EVENT_PATH", "")
    if not repository or not github_token or not event_path:
        return None
    try:
        owner, repo = repository.split("/", maxsplit=1)
        trigger = load_trigger_context_from_env(env)
        if trigger.pull_request_number is not None and trigger.head_sha:
            return {
                "owner": owner,
                "repo": repo,
                "repository": repository,
                "pull_request_number": trigger.pull_request_number,
                "head_sha": trigger.head_sha,
                "run_id": int(env.get("GITHUB_RUN_ID", "0") or "0"),
                "run_attempt": int(env.get("GITHUB_RUN_ATTEMPT", "1") or "1"),
                "tool_ref": env.get("PR_AGENT_CONTEXT_TOOL_REF", ""),
                "trigger_event_name": trigger.event_name,
                "trigger_label": trigger.label,
                "execution_mode": _resolve_execution_mode_from_env(env, trigger.event_name),
                "publish_mode": env.get("PR_AGENT_CONTEXT_PUBLISH_MODE", "append"),
                "hide_previous_managed_comments_on_append": parse_bool_env(
                    env.get("PR_AGENT_CONTEXT_HIDE_PREVIOUS_MANAGED_COMMENTS_ON_APPEND"),
                    default=True,
                ),
                "github_token": github_token,
                "github_api_url": env.get("GITHUB_API_URL", "https://api.github.com"),
                "skip_comment_on_readonly_token": parse_bool_env(
                    env.get("PR_AGENT_CONTEXT_SKIP_COMMENT_ON_READONLY_TOKEN"),
                    default=True,
                ),
            }
        client = GitHubApiClient(
            token=github_token,
            api_url=env.get("GITHUB_API_URL", "https://api.github.com"),
        )
        pull_request, _ = resolve_pull_request_ref(
            client,
            owner=owner,
            repo=repo,
            trigger=trigger,
        )
    except Exception:
        return None
    return {
        "owner": owner,
        "repo": repo,
        "repository": repository,
        "pull_request_number": pull_request.number,
        "head_sha": pull_request.head_sha,
        "run_id": int(env.get("GITHUB_RUN_ID", "0") or "0"),
        "run_attempt": int(env.get("GITHUB_RUN_ATTEMPT", "1") or "1"),
        "tool_ref": env.get("PR_AGENT_CONTEXT_TOOL_REF", ""),
        "trigger_event_name": trigger.event_name,
        "trigger_label": trigger.label,
        "execution_mode": _resolve_execution_mode_from_env(env, trigger.event_name),
        "publish_mode": env.get("PR_AGENT_CONTEXT_PUBLISH_MODE", "append"),
        "hide_previous_managed_comments_on_append": parse_bool_env(
            env.get("PR_AGENT_CONTEXT_HIDE_PREVIOUS_MANAGED_COMMENTS_ON_APPEND"),
            default=True,
        ),
        "github_token": github_token,
        "github_api_url": env.get("GITHUB_API_URL", "https://api.github.com"),
        "skip_comment_on_readonly_token": parse_bool_env(
            env.get("PR_AGENT_CONTEXT_SKIP_COMMENT_ON_READONLY_TOKEN"),
            default=True,
        ),
    }


def _build_failure_markdown(*, context: dict[str, object], error: Exception) -> str:
    run_url = (
        f"https://github.com/{context['repository']}/actions/runs/{context['run_id']}"
        if context.get("run_id")
        else ""
    )
    lines = [
        "🚨 `pr-agent-context` failed while preparing PR context.",
        "",
        f"PR: #{context['pull_request_number']}",
        f"Error: {type(error).__name__}: {error}",
    ]
    if run_url:
        lines.append(f"Run: {run_url}")
    lines.extend(
        [
            "",
            "The workflow continued gracefully so this failure does not block CI.",
            "Check the job logs for the full traceback.",
        ]
    )
    return "\n".join(lines)


def _resolve_execution_mode_from_env(env: dict[str, str], event_name: str) -> str:
    return _resolve_execution_mode(
        env.get("PR_AGENT_CONTEXT_EXECUTION_MODE", "auto") or "auto",
        event_name,
    )


def _write_failure_outputs(
    *,
    config: RunConfig | None,
    env: dict[str, str],
    publication,
) -> None:
    output_path = config.github_output_path if config else None
    if output_path is None and env.get("GITHUB_OUTPUT"):
        output_path = Path(env["GITHUB_OUTPUT"])
    if output_path is None:
        return
    lines = [
        f"comment_id={publication.comment_id if publication and publication.comment_id else ''}",
        f"comment_url={publication.comment_url if publication and publication.comment_url else ''}",
        "unresolved_thread_count=",
        "failing_check_count=",
        "has_actionable_items=false",
        "patch_coverage_percent=",
        "prompt_sha256=",
        f"comment_written={'true' if publication and publication.comment_written else 'false'}",
    ]
    try:
        output_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception as error:  # pragma: no cover - exercised via fallback tests
        print(
            json.dumps(
                {
                    "tool": "pr-agent-context",
                    "event": "fatal_error_output_write_failed",
                    "error_type": type(error).__name__,
                    "error_message": str(error),
                    "output_path": str(output_path),
                },
                sort_keys=True,
            )
        )


def _filtered_log_payload(payload: dict[str, object]) -> dict[str, object]:
    return {key: value for key, value in payload.items() if value != ""}
