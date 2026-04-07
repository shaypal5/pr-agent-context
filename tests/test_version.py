from __future__ import annotations

import importlib


def test_version_falls_back_to_pyproject_when_package_metadata_is_unavailable(
    monkeypatch,
):
    import pr_agent_context as package

    monkeypatch.setattr(
        "importlib.metadata.version",
        lambda _name: (_ for _ in ()).throw(importlib.metadata.PackageNotFoundError(_name)),
    )

    reloaded = importlib.reload(package)

    assert reloaded.__version__ == "4.0.15"


def test_read_pyproject_version_reads_from_source_checkout(tmp_path, monkeypatch):
    import pr_agent_context as package

    package_root = tmp_path / "src" / "pr_agent_context"
    package_root.mkdir(parents=True)
    fake_init = package_root / "__init__.py"
    fake_init.write_text("# placeholder\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nversion = "9.8.7"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(package, "__file__", str(fake_init))

    assert package._read_pyproject_version() == "9.8.7"


def test_read_pyproject_version_fails_when_pyproject_is_missing(tmp_path, monkeypatch):
    import pr_agent_context as package

    package_root = tmp_path / "src" / "pr_agent_context"
    package_root.mkdir(parents=True)
    fake_init = package_root / "__init__.py"
    fake_init.write_text("# placeholder\n", encoding="utf-8")
    monkeypatch.setattr(package, "__file__", str(fake_init))

    try:
        package._read_pyproject_version()
    except RuntimeError as exc:
        assert "source checkout" in str(exc)
    else:
        raise AssertionError("Expected RuntimeError when pyproject.toml is missing")


def test_read_pyproject_version_fails_when_version_is_missing(tmp_path, monkeypatch):
    import pr_agent_context as package

    package_root = tmp_path / "src" / "pr_agent_context"
    package_root.mkdir(parents=True)
    fake_init = package_root / "__init__.py"
    fake_init.write_text("# placeholder\n", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'demo'\n", encoding="utf-8")
    monkeypatch.setattr(package, "__file__", str(fake_init))

    try:
        package._read_pyproject_version()
    except RuntimeError as exc:
        assert "pyproject.toml" in str(exc)
    else:
        raise AssertionError("Expected RuntimeError when version is missing")


def test_github_api_client_defaults_user_agent_from_package_version():
    from pr_agent_context.github.api import GitHubApiClient

    client = GitHubApiClient(token="token")

    assert client._user_agent == "pr-agent-context/4.0.15"


def test_github_api_client_respects_explicit_user_agent_override():
    from pr_agent_context.github.api import GitHubApiClient

    client = GitHubApiClient(token="token", user_agent="custom-agent/1.2.3")

    assert client._user_agent == "custom-agent/1.2.3"


def test_github_api_client_preserves_explicit_empty_user_agent():
    from pr_agent_context.github.api import GitHubApiClient

    client = GitHubApiClient(token="token", user_agent="")

    assert client._user_agent == ""
