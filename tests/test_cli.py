from __future__ import annotations

import json
from pathlib import Path

import pytest

from pr_agent_context.cli import main
from pr_agent_context.domain.models import PublicationResult


def test_cli_run_invokes_service(monkeypatch):
    sentinel_config = object()

    monkeypatch.setattr("pr_agent_context.cli.RunConfig.from_env", lambda: sentinel_config)
    monkeypatch.setattr(
        "pr_agent_context.cli.run_service",
        lambda config: 7 if config is sentinel_config else 1,
    )

    assert main(["run"]) == 7


def test_cli_run_publishes_failure_comment_and_returns_zero(monkeypatch, tmp_path, capsys):
    class FakeConfig:
        tool_ref = "v2"
        github_token = "token"
        github_api_url = "https://api.github.com"
        skip_comment_on_readonly_token = True
        github_output_path = tmp_path / "github-output.txt"

        class pull_request:
            owner = "shaypal5"
            repo = "pr-agent-context"
            number = 15
            head_sha = "deadbeef"

        run_id = 123
        run_attempt = 2

    captured = {}

    monkeypatch.setattr("pr_agent_context.cli.RunConfig.from_env", lambda: FakeConfig())
    monkeypatch.setattr(
        "pr_agent_context.cli.run_service",
        lambda config: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    monkeypatch.setattr("pr_agent_context.cli.GitHubApiClient", lambda token, api_url: object())

    def fake_sync_managed_comment(
        client,
        *,
        owner,
        repo,
        pull_request_number,
        body,
        delete_comment_when_empty,
        skip_comment_on_readonly_token,
    ):
        captured["body"] = body
        captured["owner"] = owner
        captured["repo"] = repo
        captured["pull_request_number"] = pull_request_number
        return PublicationResult(
            comment_id=500,
            comment_url="https://github.com/shaypal5/pr-agent-context/pull/15#issuecomment-500",
            comment_written=True,
            action="created",
        )

    monkeypatch.setattr("pr_agent_context.cli.sync_managed_comment", fake_sync_managed_comment)

    assert main(["run"]) == 0

    stdout = capsys.readouterr().out
    events = [json.loads(line) for line in stdout.splitlines() if line.startswith("{")]
    assert any(event["event"] == "fatal_error" for event in events)
    comment_sync_event = next(
        event for event in events if event["event"] == "fatal_error_comment_sync"
    )
    assert "skipped_reason" not in comment_sync_event
    assert "error_status_code" not in comment_sync_event
    assert "🚨 `pr-agent-context` failed while preparing PR context." in captured["body"]
    assert "Head commit: deadbeef" in captured["body"]
    assert "Version:" in captured["body"]
    outputs = Path(FakeConfig.github_output_path).read_text(encoding="utf-8")
    assert "comment_written=true" in outputs
    assert "comment_id=500" in outputs


def test_cli_run_returns_zero_when_failure_comment_sync_fails(monkeypatch, capsys):
    class FakeConfig:
        tool_ref = "v2"
        github_token = "token"
        github_api_url = "https://api.github.com"
        skip_comment_on_readonly_token = True
        github_output_path = None

        class pull_request:
            owner = "shaypal5"
            repo = "pr-agent-context"
            number = 15
            head_sha = "deadbeef"

        run_id = 123
        run_attempt = 2

    monkeypatch.setattr("pr_agent_context.cli.RunConfig.from_env", lambda: FakeConfig())
    monkeypatch.setattr(
        "pr_agent_context.cli.run_service",
        lambda config: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    monkeypatch.setattr("pr_agent_context.cli.GitHubApiClient", lambda token, api_url: object())
    monkeypatch.setattr(
        "pr_agent_context.cli.sync_managed_comment",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("comment sync failed")),
    )

    assert main(["run"]) == 0

    stdout = capsys.readouterr().out
    events = [json.loads(line) for line in stdout.splitlines() if line.startswith("{")]
    assert any(event["event"] == "fatal_error_comment_sync_failed" for event in events)


def test_cli_run_handles_config_load_failure_with_env_derived_context(
    monkeypatch,
    tmp_path,
    capsys,
):
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps(
            {
                "pull_request": {
                    "number": 17,
                    "base": {"sha": "abc123"},
                    "head": {"sha": "def456"},
                }
            }
        ),
        encoding="utf-8",
    )
    output_path = tmp_path / "github-output.txt"

    monkeypatch.setattr(
        "pr_agent_context.cli.RunConfig.from_env",
        lambda: (_ for _ in ()).throw(ValueError("bad config")),
    )
    monkeypatch.setattr("pr_agent_context.cli.GitHubApiClient", lambda token, api_url: object())
    monkeypatch.setenv("GITHUB_REPOSITORY", "shaypal5/pr-agent-context")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("GITHUB_RUN_ID", "321")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "4")
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_path))
    monkeypatch.setenv("PR_AGENT_CONTEXT_TOOL_REF", "v2")
    monkeypatch.setenv("PR_AGENT_CONTEXT_SKIP_COMMENT_ON_READONLY_TOKEN", "false")

    captured = {}

    def fake_sync_managed_comment(
        client,
        *,
        owner,
        repo,
        pull_request_number,
        body,
        delete_comment_when_empty,
        skip_comment_on_readonly_token,
    ):
        captured["owner"] = owner
        captured["repo"] = repo
        captured["pull_request_number"] = pull_request_number
        captured["skip_comment_on_readonly_token"] = skip_comment_on_readonly_token
        captured["body"] = body
        return PublicationResult(
            comment_id=777,
            comment_url="https://github.com/shaypal5/pr-agent-context/pull/17#issuecomment-777",
            comment_written=True,
            action="created",
        )

    monkeypatch.setattr("pr_agent_context.cli.sync_managed_comment", fake_sync_managed_comment)

    assert main(["run"]) == 0

    stdout = capsys.readouterr().out
    events = [json.loads(line) for line in stdout.splitlines() if line.startswith("{")]
    fatal_event = next(event for event in events if event["event"] == "fatal_error")
    assert fatal_event["pull_request_number"] == 17
    assert fatal_event["head_sha"] == "def456"
    assert fatal_event["run_id"] == 321
    assert captured["owner"] == "shaypal5"
    assert captured["repo"] == "pr-agent-context"
    assert captured["pull_request_number"] == 17
    assert captured["skip_comment_on_readonly_token"] is False
    outputs = output_path.read_text(encoding="utf-8")
    assert "comment_written=true" in outputs
    assert "comment_id=777" in outputs


def test_cli_main_rejects_unsupported_command(monkeypatch):
    monkeypatch.setattr(
        "pr_agent_context.cli.build_parser",
        lambda: type(
            "FakeParser",
            (),
            {
                "parse_args": lambda self, argv=None: type("Args", (), {"command": "bad"})(),
                "error": lambda self, message: (_ for _ in ()).throw(SystemExit(2)),
            },
        )(),
    )

    with pytest.raises(SystemExit) as error:
        main(["bad"])

    assert error.value.code == 2


def test_cli_main_returns_two_if_parser_error_returns_normally(monkeypatch):
    monkeypatch.setattr(
        "pr_agent_context.cli.build_parser",
        lambda: type(
            "FakeParser",
            (),
            {
                "parse_args": lambda self, argv=None: type("Args", (), {"command": "bad"})(),
                "error": lambda self, message: None,
            },
        )(),
    )

    assert main(["bad"]) == 2


def test_resolve_failure_context_returns_none_without_required_env(monkeypatch):
    from pr_agent_context.cli import _resolve_failure_context

    assert _resolve_failure_context(config=None, env={}) is None


def test_resolve_failure_context_returns_none_for_invalid_event_payload(tmp_path):
    from pr_agent_context.cli import _resolve_failure_context

    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps({"pull_request": {"base": {}, "head": {}}}), encoding="utf-8")

    context = _resolve_failure_context(
        config=None,
        env={
            "GITHUB_REPOSITORY": "shaypal5/pr-agent-context",
            "GITHUB_TOKEN": "token",
            "GITHUB_EVENT_PATH": str(event_path),
        },
    )

    assert context is None
