from __future__ import annotations

import importlib


def test_version_falls_back_to_pyproject_when_package_metadata_is_unavailable(monkeypatch):
    import pr_agent_context as package

    monkeypatch.setattr(
        "importlib.metadata.version",
        lambda _name: (_ for _ in ()).throw(importlib.metadata.PackageNotFoundError()),
    )

    reloaded = importlib.reload(package)

    assert reloaded.__version__ == "0.1.1"


def test_github_api_client_defaults_user_agent_from_package_version():
    from pr_agent_context.github.api import GitHubApiClient

    client = GitHubApiClient(token="token")

    assert client._user_agent == "pr-agent-context/0.1.1"
