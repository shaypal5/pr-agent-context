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
        self._user_agent = user_agent or f"pr-agent-context/{__version__}"

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

    def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None,
        payload: dict[str, Any] | list[Any] | None,
        extra_headers: dict[str, str] | None,
    ) -> bytes:
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
        try:
            with urllib.request.urlopen(request) as response:
                return response.read()
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise GitHubApiError(exc.code, exc.reason, body) from exc
