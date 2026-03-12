from __future__ import annotations

import json
from pathlib import Path

import pytest

from pr_agent_context.cli import _handle_run_failure, _resolve_failure_context, main
from pr_agent_context.config import PullRequestRef
from pr_agent_context.domain.models import PublicationResult


def test_cli_run_invokes_service(monkeypatch):
    sentinel_config = object()

    monkeypatch.setattr(
        "pr_agent_context.cli.RunConfig.from_env", lambda: sentinel_config
    )
    monkeypatch.setattr(
        "pr_agent_context.cli.run_service",
        lambda config: 7 if config is sentinel_config else 1,
    )

    assert main(["run"]) == 7


def test_cli_run_publishes_failure_comment_and_returns_zero(
    monkeypatch, tmp_path, capsys
):
    class FakeConfig:
        tool_ref = "v4"
        github_token = "token"
        github_api_url = "https://api.github.com"
        skip_comment_on_readonly_token = True
        github_output_path = tmp_path / "github-output.txt"
        publish_mode = "append"

        class trigger:
            event_name = "pull_request"

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
    monkeypatch.setattr(
        "pr_agent_context.cli.GitHubApiClient", lambda token, api_url: object()
    )

    def fake_sync_managed_comment(
        client,
        *,
        owner,
        repo,
        pull_request_number,
        run_id,
        run_attempt,
        head_sha,
        tool_ref,
        trigger_event_name,
        publish_mode,
        generated_at,
        body,
        delete_comment_when_empty,
        skip_comment_on_readonly_token,
    ):
        captured["body"] = body
        captured["owner"] = owner
        captured["repo"] = repo
        captured["pull_request_number"] = pull_request_number
        captured["run_id"] = run_id
        captured["run_attempt"] = run_attempt
        captured["head_sha"] = head_sha
        captured["tool_ref"] = tool_ref
        captured["trigger_event_name"] = trigger_event_name
        captured["publish_mode"] = publish_mode
        captured["generated_at"] = generated_at
        return PublicationResult(
            comment_id=500,
            comment_url="https://github.com/shaypal5/pr-agent-context/pull/15#issuecomment-500",
            comment_written=True,
            action="created",
        )

    monkeypatch.setattr(
        "pr_agent_context.cli.sync_managed_comment", fake_sync_managed_comment
    )

    assert main(["run"]) == 0

    stdout = capsys.readouterr().out
    events = [json.loads(line) for line in stdout.splitlines() if line.startswith("{")]
    assert any(event["event"] == "fatal_error" for event in events)
    comment_sync_event = next(
        event for event in events if event["event"] == "fatal_error_comment_sync"
    )
    assert "skipped_reason" not in comment_sync_event
    assert "error_status_code" not in comment_sync_event
    assert captured["body"].startswith(
        "<!-- pr-agent-context:managed-comment; schema=v4; publish_mode=append; "
        "pr=15; head_sha=deadbeef; trigger_event=pull_request; generated_at="
    )
    assert "\npr-agent-context report:\n```markdown\n" in captured["body"]
    assert (
        "🚨 `pr-agent-context` failed while preparing PR context." in captured["body"]
    )
    assert "\nRun metadata:\n```\nTool ref: v4\nTool version:" in captured["body"]
    assert "Workflow run: 123 attempt 2" in captured["body"]
    assert "PR head commit: deadbeef" in captured["body"]
    assert captured["run_id"] == 123
    assert captured["run_attempt"] == 2
    assert captured["generated_at"] is not None
    outputs = Path(FakeConfig.github_output_path).read_text(encoding="utf-8")
    assert "comment_written=true" in outputs
    assert "comment_id=500" in outputs


def test_cli_run_returns_zero_when_failure_comment_sync_fails(monkeypatch, capsys):
    class FakeConfig:
        tool_ref = "v4"
        github_token = "token"
        github_api_url = "https://api.github.com"
        skip_comment_on_readonly_token = True
        github_output_path = None
        publish_mode = "append"

        class trigger:
            event_name = "pull_request"

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
    monkeypatch.setattr(
        "pr_agent_context.cli.GitHubApiClient", lambda token, api_url: object()
    )
    monkeypatch.setattr(
        "pr_agent_context.cli.sync_managed_comment",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("comment sync failed")
        ),
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
    monkeypatch.setattr(
        "pr_agent_context.cli.GitHubApiClient", lambda token, api_url: object()
    )
    monkeypatch.setenv("GITHUB_REPOSITORY", "shaypal5/pr-agent-context")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("GITHUB_RUN_ID", "321")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "4")
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setenv("GITHUB_OUTPUT", str(output_path))
    monkeypatch.setenv("PR_AGENT_CONTEXT_TOOL_REF", "v4")
    monkeypatch.setenv("PR_AGENT_CONTEXT_SKIP_COMMENT_ON_READONLY_TOKEN", "false")

    captured = {}

    def fake_sync_managed_comment(
        client,
        *,
        owner,
        repo,
        pull_request_number,
        run_id,
        run_attempt,
        head_sha,
        tool_ref,
        trigger_event_name,
        publish_mode,
        generated_at,
        body,
        delete_comment_when_empty,
        skip_comment_on_readonly_token,
    ):
        captured["owner"] = owner
        captured["repo"] = repo
        captured["pull_request_number"] = pull_request_number
        captured["skip_comment_on_readonly_token"] = skip_comment_on_readonly_token
        captured["body"] = body
        captured["run_id"] = run_id
        captured["run_attempt"] = run_attempt
        captured["head_sha"] = head_sha
        captured["tool_ref"] = tool_ref
        captured["trigger_event_name"] = trigger_event_name
        captured["publish_mode"] = publish_mode
        captured["generated_at"] = generated_at
        return PublicationResult(
            comment_id=777,
            comment_url="https://github.com/shaypal5/pr-agent-context/pull/17#issuecomment-777",
            comment_written=True,
            action="created",
        )

    monkeypatch.setattr(
        "pr_agent_context.cli.sync_managed_comment", fake_sync_managed_comment
    )

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
    assert captured["run_id"] == 321
    assert captured["run_attempt"] == 4
    assert captured["generated_at"] is not None
    outputs = output_path.read_text(encoding="utf-8")
    assert "comment_written=true" in outputs
    assert "comment_id=777" in outputs


def test_cli_run_ignores_output_write_failure_in_fallback_path(
    monkeypatch, tmp_path, capsys
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

    monkeypatch.setattr(
        "pr_agent_context.cli.RunConfig.from_env",
        lambda: (_ for _ in ()).throw(ValueError("bad config")),
    )
    monkeypatch.setattr(
        "pr_agent_context.cli.GitHubApiClient", lambda token, api_url: object()
    )
    monkeypatch.setenv("GITHUB_REPOSITORY", "shaypal5/pr-agent-context")
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event_path))
    monkeypatch.setenv("GITHUB_RUN_ID", "321")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "4")
    monkeypatch.setenv("GITHUB_TOKEN", "token")
    monkeypatch.setenv("GITHUB_OUTPUT", str(tmp_path / "github-output.txt"))

    def fail_write_text(self, data, encoding="utf-8"):  # noqa: ARG001
        raise OSError("disk full")

    monkeypatch.setattr("pathlib.Path.write_text", fail_write_text)
    monkeypatch.setattr(
        "pr_agent_context.cli.sync_managed_comment",
        lambda *args, **kwargs: PublicationResult(
            comment_id=777,
            comment_url="https://github.com/shaypal5/pr-agent-context/pull/17#issuecomment-777",
            comment_written=True,
            action="created",
        ),
    )

    assert main(["run"]) == 0

    stdout = capsys.readouterr().out
    events = [json.loads(line) for line in stdout.splitlines() if line.startswith("{")]
    assert any(event["event"] == "fatal_error_output_write_failed" for event in events)


def test_cli_main_rejects_unsupported_command(monkeypatch):
    monkeypatch.setattr(
        "pr_agent_context.cli.build_parser",
        lambda: type(
            "FakeParser",
            (),
            {
                "parse_args": lambda self, argv=None: type(
                    "Args", (), {"command": "bad"}
                )(),
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
                "parse_args": lambda self, argv=None: type(
                    "Args", (), {"command": "bad"}
                )(),
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
    event_path.write_text(
        json.dumps({"pull_request": {"base": {}, "head": {}}}), encoding="utf-8"
    )

    context = _resolve_failure_context(
        config=None,
        env={
            "GITHUB_REPOSITORY": "shaypal5/pr-agent-context",
            "GITHUB_TOKEN": "token",
            "GITHUB_EVENT_PATH": str(event_path),
        },
    )

    assert context is None


def test_resolve_failure_context_falls_back_to_env_when_config_pull_request_is_none(
    monkeypatch,
    tmp_path,
):
    from pr_agent_context.cli import _resolve_failure_context

    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps(
            {
                "action": "submitted",
                "pull_request": {
                    "number": 17,
                    "base": {"sha": "abc123"},
                    "head": {"sha": "def456"},
                },
            }
        ),
        encoding="utf-8",
    )

    class FakeConfig:
        pull_request = None
        tool_ref = "v4"
        github_token = "token"
        github_api_url = "https://api.github.com"
        skip_comment_on_readonly_token = True
        publish_mode = "append"

        class trigger:
            event_name = "pull_request_review"

        run_id = 123
        run_attempt = 2

    context = _resolve_failure_context(
        config=FakeConfig(),
        env={
            "GITHUB_REPOSITORY": "shaypal5/pr-agent-context",
            "GITHUB_TOKEN": "token",
            "GITHUB_EVENT_PATH": str(event_path),
            "GITHUB_EVENT_NAME": "pull_request_review",
            "GITHUB_RUN_ID": "321",
            "GITHUB_RUN_ATTEMPT": "4",
            "PR_AGENT_CONTEXT_TOOL_REF": "v4",
        },
    )

    assert context is not None
    assert context["pull_request_number"] == 17
    assert context["head_sha"] == "def456"
    assert context["trigger_event_name"] == "pull_request_review"


def test_resolve_failure_context_prefers_config_pull_request(tmp_path):
    from pr_agent_context.cli import _resolve_failure_context

    class FakeConfig:
        tool_ref = "v4"
        github_token = "token"
        github_api_url = "https://api.github.com"
        skip_comment_on_readonly_token = False
        publish_mode = "append"

        class trigger:
            event_name = "pull_request"

        class pull_request:
            owner = "shaypal5"
            repo = "pr-agent-context"
            number = 17
            head_sha = "def456"

        run_id = 123
        run_attempt = 2

    context = _resolve_failure_context(
        config=FakeConfig(),
        env={"GITHUB_REPOSITORY": "ignored/repo", "GITHUB_TOKEN": "token"},
    )

    assert context is not None
    assert context["repository"] == "shaypal5/pr-agent-context"
    assert context["head_sha"] == "def456"
    assert context["skip_comment_on_readonly_token"] is False


def test_build_failure_markdown_omits_run_url_when_run_id_missing():
    from pr_agent_context.cli import _build_failure_markdown

    markdown = _build_failure_markdown(
        context={
            "repository": "shaypal5/pr-agent-context",
            "pull_request_number": 17,
            "run_id": 0,
        },
        error=RuntimeError("boom"),
    )

    assert "Run:" not in markdown


def test_handle_run_failure_skips_comment_sync_without_context(monkeypatch, capsys):
    monkeypatch.setattr("pr_agent_context.cli.traceback.print_exc", lambda: None)
    monkeypatch.setattr("pr_agent_context.cli.os.environ", {}, raising=False)

    _handle_run_failure(RuntimeError("boom"), config=None)

    stdout = capsys.readouterr().out
    assert '"event": "fatal_error"' in stdout
    assert "fatal_error_comment_sync" not in stdout


def test_resolve_failure_context_falls_back_to_resolving_pull_request_from_trigger(
    monkeypatch,
    tmp_path,
):
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps({"workflow_run": {"head_sha": "def456", "pull_requests": []}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "pr_agent_context.cli.resolve_pull_request_ref",
        lambda client, owner, repo, trigger: (  # noqa: ARG005
            PullRequestRef(
                owner=owner,
                repo=repo,
                number=17,
                base_sha="abc123",
                head_sha="def456",
            ),
            {},
        ),
    )
    monkeypatch.setattr(
        "pr_agent_context.cli.GitHubApiClient", lambda token, api_url: object()
    )

    context = _resolve_failure_context(
        config=None,
        env={
            "GITHUB_REPOSITORY": "shaypal5/pr-agent-context",
            "GITHUB_EVENT_PATH": str(event_path),
            "GITHUB_EVENT_NAME": "workflow_run",
            "GITHUB_RUN_ID": "321",
            "GITHUB_RUN_ATTEMPT": "4",
            "GITHUB_TOKEN": "token",
        },
    )

    assert context is not None
    assert context["pull_request_number"] == 17
    assert context["head_sha"] == "def456"
