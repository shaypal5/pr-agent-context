from __future__ import annotations

from pr_agent_context.cli import main


def test_cli_run_invokes_service(monkeypatch):
    sentinel_config = object()

    monkeypatch.setattr("pr_agent_context.cli.RunConfig.from_env", lambda: sentinel_config)
    monkeypatch.setattr(
        "pr_agent_context.cli.run_service",
        lambda config: 7 if config is sentinel_config else 1,
    )

    assert main(["run"]) == 7
