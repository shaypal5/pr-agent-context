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
    assert (
        request.full_url
        == "https://api.github.com/repos/shaypal5/example/issues?per_page=10&page=2"
    )
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
    assert client.graphql("query Viewer { viewer { login } }", {}) == {
        "viewer": {"login": "octocat"}
    }

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


def test_request_bytes_following_redirect_strips_github_auth_headers_on_cross_host(
    monkeypatch,
):
    captured = {}
    redirect_error = urllib.error.HTTPError(
        url="https://api.github.com/repos/shaypal5/example/actions/artifacts/17/zip",
        code=302,
        msg="Found",
        hdrs={"Location": "https://artifactcache.actions.githubusercontent.com/blob.zip?sig=123"},
        fp=io.BytesIO(b""),
    )

    class _FakeOpener:
        def open(self, request):
            captured["initial_request"] = request
            raise redirect_error

    def fake_urlopen(request):
        captured["redirect_request"] = request
        return _FakeResponse(b"zip-bytes")

    monkeypatch.setattr("urllib.request.build_opener", lambda *handlers: _FakeOpener())
    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    client = GitHubApiClient(token="secret")

    response = client.request_bytes_following_redirect_without_auth(
        "GET",
        "/repos/shaypal5/example/actions/artifacts/17/zip",
    )

    assert response == b"zip-bytes"
    assert captured["initial_request"].headers["Authorization"] == "Bearer secret"
    assert "Authorization" not in captured["redirect_request"].headers
    assert "X-github-api-version" not in captured["redirect_request"].headers
    assert captured["redirect_request"].full_url.startswith(
        "https://artifactcache.actions.githubusercontent.com/"
    )


def test_request_bytes_following_redirect_returns_direct_response_when_not_redirected(monkeypatch):
    monkeypatch.setattr(
        GitHubApiClient,
        "_request_redirect_location",
        lambda self, request: None,  # noqa: ARG005
    )
    monkeypatch.setattr(
        GitHubApiClient,
        "_open_request",
        lambda self, request: b"direct-bytes",  # noqa: ARG005
    )

    client = GitHubApiClient(token="secret")

    assert (
        client.request_bytes_following_redirect_without_auth(
            "GET",
            "/repos/shaypal5/example/actions/artifacts/17/zip",
        )
        == b"direct-bytes"
    )


def test_request_bytes_following_redirect_rejects_non_get_methods():
    client = GitHubApiClient(token="secret")

    with pytest.raises(
        ValueError,
        match="only supports GET requests",
    ):
        client.request_bytes_following_redirect_without_auth(
            "POST",
            "/repos/shaypal5/example/actions/artifacts/17/zip",
        )


def test_request_redirect_location_raises_for_non_redirect_http_errors(monkeypatch):
    error = urllib.error.HTTPError(
        url="https://api.github.com/repos/shaypal5/example/actions/artifacts/17/zip",
        code=401,
        msg="Unauthorized",
        hdrs={},
        fp=io.BytesIO(b"not allowed"),
    )

    class _FakeOpener:
        def open(self, request):  # noqa: ARG002
            raise error

    monkeypatch.setattr("urllib.request.build_opener", lambda *handlers: _FakeOpener())

    client = GitHubApiClient(token="secret")
    request, _headers = client._build_request(  # noqa: SLF001
        "GET",
        "/repos/shaypal5/example/actions/artifacts/17/zip",
        params=None,
        payload=None,
        extra_headers=None,
    )

    with pytest.raises(GitHubApiError, match="Unauthorized"):
        client._request_redirect_location(request)  # noqa: SLF001


def test_request_redirect_location_handles_missing_headers_without_attribute_error(monkeypatch):
    error = urllib.error.HTTPError(
        url="https://api.github.com/repos/shaypal5/example/actions/artifacts/17/zip",
        code=302,
        msg="Found",
        hdrs=None,
        fp=io.BytesIO(b"missing location"),
    )

    class _FakeOpener:
        def open(self, request):  # noqa: ARG002
            raise error

    monkeypatch.setattr("urllib.request.build_opener", lambda *handlers: _FakeOpener())

    client = GitHubApiClient(token="secret")
    request, _headers = client._build_request(  # noqa: SLF001
        "GET",
        "/repos/shaypal5/example/actions/artifacts/17/zip",
        params=None,
        payload=None,
        extra_headers=None,
    )

    with pytest.raises(GitHubApiError, match="Found"):
        client._request_redirect_location(request)  # noqa: SLF001


def test_request_redirect_location_returns_none_when_response_is_not_redirected(monkeypatch):
    class _FakeOpener:
        def open(self, request):  # noqa: ARG002
            return _FakeResponse(b"ok")

    monkeypatch.setattr("urllib.request.build_opener", lambda *handlers: _FakeOpener())

    client = GitHubApiClient(token="secret")
    request, _headers = client._build_request(  # noqa: SLF001
        "GET",
        "/repos/shaypal5/example/actions/artifacts/17/zip",
        params=None,
        payload=None,
        extra_headers=None,
    )

    assert client._request_redirect_location(request) is None  # noqa: SLF001


def test_request_redirect_location_raises_when_redirect_has_no_location(monkeypatch):
    error = urllib.error.HTTPError(
        url="https://api.github.com/repos/shaypal5/example/actions/artifacts/17/zip",
        code=302,
        msg="Found",
        hdrs={},
        fp=io.BytesIO(b"missing location"),
    )

    class _FakeOpener:
        def open(self, request):  # noqa: ARG002
            raise error

    monkeypatch.setattr("urllib.request.build_opener", lambda *handlers: _FakeOpener())

    client = GitHubApiClient(token="secret")
    request, _headers = client._build_request(  # noqa: SLF001
        "GET",
        "/repos/shaypal5/example/actions/artifacts/17/zip",
        params=None,
        payload=None,
        extra_headers=None,
    )

    with pytest.raises(GitHubApiError, match="Found"):
        client._request_redirect_location(request)  # noqa: SLF001


def test_build_redirect_headers_keeps_auth_for_same_host_redirect():
    client = GitHubApiClient(token="secret")

    headers = client._build_redirect_headers(  # noqa: SLF001
        source_url="https://api.github.com/source",
        destination_url="https://api.github.com/redirected",
        headers={
            "Authorization": "Bearer secret",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "test-agent",
        },
    )

    assert headers["Authorization"] == "Bearer secret"
    assert headers["Accept"] == "application/vnd.github+json"


def test_no_redirect_handler_returns_none():
    from pr_agent_context.github.api import _NoRedirectHandler

    handler = _NoRedirectHandler()

    assert handler.redirect_request(None, None, 302, "Found", {}, "https://example.com") is None


def test_request_redirect_location_closes_http_error_when_location_is_present(monkeypatch):
    closed = {"value": False}

    class _ClosableRedirectError(urllib.error.HTTPError):
        def close(self):
            closed["value"] = True
            return super().close()

    error = _ClosableRedirectError(
        url="https://api.github.com/repos/shaypal5/example/actions/artifacts/17/zip",
        code=302,
        msg="Found",
        hdrs={"Location": "https://artifactcache.actions.githubusercontent.com/blob.zip?sig=123"},
        fp=io.BytesIO(b""),
    )

    class _FakeOpener:
        def open(self, request):  # noqa: ARG002
            raise error

    monkeypatch.setattr("urllib.request.build_opener", lambda *handlers: _FakeOpener())

    client = GitHubApiClient(token="secret")
    request, _headers = client._build_request(  # noqa: SLF001
        "GET",
        "/repos/shaypal5/example/actions/artifacts/17/zip",
        params=None,
        payload=None,
        extra_headers=None,
    )

    assert client._request_redirect_location(request) == (
        "https://artifactcache.actions.githubusercontent.com/blob.zip?sig=123"
    )
    assert closed["value"] is True
