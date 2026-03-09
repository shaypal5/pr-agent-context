from __future__ import annotations

import json
from pathlib import Path

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
    assert any(event["event"] == "fatal_error_comment_sync" for event in events)
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
