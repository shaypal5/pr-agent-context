from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from pr_agent_context import __version__


@dataclass(slots=True)
class GitHubApiError(RuntimeError):
    status_code: int
    message: str
    body: str = ""

    def __str__(self) -> str:
        return f"GitHub API error {self.status_code}: {self.message}"


class GitHubApiClient:
    def __init__(
        self,
        *,
        token: str,
        api_url: str = "https://api.github.com",
        user_agent: str | None = None,
    ) -> None:
        self._token = token
        self._api_url = api_url.rstrip("/")
        self._user_agent = f"pr-agent-context/{__version__}" if user_agent is None else user_agent

    def graphql(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        payload = {"query": query, "variables": variables}
        response = self.request_json("POST", "/graphql", payload=payload)
        if response.get("errors"):
            raise GitHubApiError(400, "GraphQL query failed", json.dumps(response["errors"]))
        return response["data"]

    def request_json(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        payload: dict[str, Any] | list[Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        raw = self._request(
            method,
            path,
            params=params,
            payload=payload,
            extra_headers=extra_headers,
        )
        if not raw:
            return {}
        return json.loads(raw)

    def request_text(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> str:
        raw = self._request(
            method,
            path,
            params=params,
            payload=None,
            extra_headers=extra_headers,
        )
        return raw.decode("utf-8", errors="replace")

    def request_bytes(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> bytes:
        return self._request(
            method,
            path,
            params=params,
            payload=None,
            extra_headers=extra_headers,
        )

    def request_bytes_following_redirect_without_auth(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> bytes:
        request, headers = self._build_request(
            method,
            path,
            params=params,
            payload=None,
            extra_headers=extra_headers,
        )
        redirect_location = self._request_redirect_location(request)
        if redirect_location is None:
            return self._open_request(request)

        redirect_headers = self._build_redirect_headers(
            source_url=request.full_url,
            destination_url=redirect_location,
            headers=headers,
        )
        redirect_request = urllib.request.Request(
            redirect_location,
            headers=redirect_headers,
            method=method,
        )
        return self._open_request(redirect_request)

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None,
        payload: dict[str, Any] | list[Any] | None,
        extra_headers: dict[str, str] | None,
    ) -> bytes:
        request, _headers = self._build_request(
            method,
            path,
            params=params,
            payload=payload,
            extra_headers=extra_headers,
        )
        return self._open_request(request)

    def _build_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None,
        payload: dict[str, Any] | list[Any] | None,
        extra_headers: dict[str, str] | None,
    ) -> tuple[urllib.request.Request, dict[str, str]]:
        encoded_params = ""
        if params:
            encoded_params = "?" + urllib.parse.urlencode(params)

        data = None
        headers = {
            "Accept": "application/vnd.github+json",
            "User-Agent": self._user_agent,
            "X-GitHub-Api-Version": "2022-11-28",
        }
        if self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        if payload is not None:
            data = json.dumps(payload).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if extra_headers:
            headers.update(extra_headers)

        request = urllib.request.Request(
            f"{self._api_url}{path}{encoded_params}",
            data=data,
            headers=headers,
            method=method,
        )
        return request, headers

    def _request_redirect_location(self, request: urllib.request.Request) -> str | None:
        opener = urllib.request.build_opener(_NoRedirectHandler())
        try:
            with opener.open(request):
                return None
        except urllib.error.HTTPError as exc:
            if exc.code in {301, 302, 303, 307, 308}:
                location = exc.headers.get("Location")
                if location:
                    return urllib.parse.urljoin(request.full_url, location)
            body = exc.read().decode("utf-8", errors="replace")
            raise GitHubApiError(exc.code, exc.reason, body) from exc

    def _build_redirect_headers(
        self,
        *,
        source_url: str,
        destination_url: str,
        headers: dict[str, str],
    ) -> dict[str, str]:
        source_host = urllib.parse.urlparse(source_url).netloc
        destination_host = urllib.parse.urlparse(destination_url).netloc
        if source_host == destination_host:
            return dict(headers)

        filtered_headers = {
            name: value
            for name, value in headers.items()
            if name not in {"Authorization", "Accept", "X-GitHub-Api-Version"}
        }
        filtered_headers.setdefault("User-Agent", self._user_agent)
        return filtered_headers

    def _open_request(self, request: urllib.request.Request) -> bytes:
        try:
            with urllib.request.urlopen(request) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise GitHubApiError(exc.code, exc.reason, body) from exc


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001, ARG002
        return None
