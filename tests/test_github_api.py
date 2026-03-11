from __future__ import annotations

import io
import urllib.error

import pytest

from pr_agent_context.github.api import GitHubApiClient, GitHubApiError


class _FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:  # noqa: ANN001, ARG002
        return False

    def read(self) -> bytes:
        return self.payload


def test_request_json_sends_headers_params_and_payload(monkeypatch):
    captured = {}

    def fake_urlopen(request):
        captured["request"] = request
        return _FakeResponse(b'{"ok": true}')

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    client = GitHubApiClient(token="secret", api_url="https://api.github.com/")
    response = client.request_json(
        "POST",
        "/repos/shaypal5/example/issues",
        params={"per_page": 10, "page": 2},
        payload={"body": "hello"},
        extra_headers={"X-Test": "1"},
    )

    request = captured["request"]
    assert response == {"ok": True}
    assert request.full_url == "https://api.github.com/repos/shaypal5/example/issues?per_page=10&page=2"
    assert request.get_method() == "POST"
    assert request.headers["Authorization"] == "Bearer secret"
    assert request.headers["Content-type"] == "application/json"
    assert request.headers["X-test"] == "1"
    assert request.data == b'{"body": "hello"}'


def test_request_json_returns_empty_dict_for_empty_response(monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda request: _FakeResponse(b""))  # noqa: ARG005

    client = GitHubApiClient(token="secret")

    assert client.request_json("GET", "/repos/shaypal5/example") == {}


def test_request_text_and_bytes_support_decoding_and_missing_auth(monkeypatch):
    captured = {}

    def fake_urlopen(request):
        captured["request"] = request
        return _FakeResponse(b"\xffhello")

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    client = GitHubApiClient(token="")

    assert client.request_text("GET", "/text") == "\ufffdhello"
    assert client.request_bytes("GET", "/bytes") == b"\xffhello"
    assert "Authorization" not in captured["request"].headers


def test_graphql_returns_data_and_raises_for_graphql_errors(monkeypatch):
    client = GitHubApiClient(token="secret")
    monkeypatch.setattr(
        client,
        "request_json",
        lambda method, path, payload=None: {"data": {"viewer": {"login": "octocat"}}},  # noqa: ARG005
    )
    assert client.graphql("query Viewer { viewer { login } }", {}) == {"viewer": {"login": "octocat"}}

    monkeypatch.setattr(
        client,
        "request_json",
        lambda method, path, payload=None: {"errors": [{"message": "boom"}], "data": {}},  # noqa: ARG005
    )
    with pytest.raises(GitHubApiError, match="GraphQL query failed"):
        client.graphql("query Viewer { viewer { login } }", {})


def test_request_translates_http_error_to_github_api_error(monkeypatch):
    error = urllib.error.HTTPError(
        url="https://api.github.com/repos/shaypal5/example",
        code=502,
        msg="Bad Gateway",
        hdrs=None,
        fp=io.BytesIO(b"upstream unavailable"),
    )

    def fake_urlopen(request):
        raise error

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    client = GitHubApiClient(token="secret")

    with pytest.raises(GitHubApiError) as exc_info:
        client.request_json("GET", "/repos/shaypal5/example")

    assert exc_info.value.status_code == 502
    assert exc_info.value.body == "upstream unavailable"
