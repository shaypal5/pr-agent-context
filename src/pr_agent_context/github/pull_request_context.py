from __future__ import annotations

from pr_agent_context.config import PullRequestRef, TriggerContext
from pr_agent_context.github.api import GitHubApiClient, GitHubApiError


def resolve_pull_request_ref(
    client: GitHubApiClient,
    *,
    owner: str,
    repo: str,
    trigger: TriggerContext,
    pull_request_hint: PullRequestRef | None = None,
) -> tuple[PullRequestRef, dict[str, object]]:
    debug = {
        "trigger": trigger.model_dump(mode="json"),
        "resolution": "",
        "warnings": [],
    }

    if pull_request_hint is not None:
        debug["resolution"] = "env_payload_complete"
        return pull_request_hint, debug

    if trigger.pull_request_number is not None:
        pull_request = _fetch_pull_request(
            client,
            owner=owner,
            repo=repo,
            pull_request_number=trigger.pull_request_number,
        )
        debug["resolution"] = "pull_request_number_lookup"
        return pull_request, debug

    if trigger.head_sha:
        pull_request = _fetch_pull_request_for_head_sha(
            client,
            owner=owner,
            repo=repo,
            head_sha=trigger.head_sha,
        )
        debug["resolution"] = "head_sha_lookup"
        return pull_request, debug

    raise ValueError("Unable to resolve pull request context for this event.")


def _fetch_pull_request(
    client: GitHubApiClient,
    *,
    owner: str,
    repo: str,
    pull_request_number: int,
) -> PullRequestRef:
    payload = client.request_json(
        "GET",
        f"/repos/{owner}/{repo}/pulls/{pull_request_number}",
    )
    base = payload.get("base") or {}
    head = payload.get("head") or {}
    base_sha = str(base.get("sha") or "").strip()
    head_sha = str(head.get("sha") or "").strip()
    if not base_sha or not head_sha:
        raise ValueError(f"Pull request #{pull_request_number} is missing base/head SHAs.")
    return PullRequestRef(
        owner=owner,
        repo=repo,
        number=pull_request_number,
        base_sha=base_sha,
        head_sha=head_sha,
    )


def _fetch_pull_request_for_head_sha(
    client: GitHubApiClient,
    *,
    owner: str,
    repo: str,
    head_sha: str,
) -> PullRequestRef:
    payload = client.request_json(
        "GET",
        f"/repos/{owner}/{repo}/commits/{head_sha}/pulls",
        extra_headers={"Accept": "application/vnd.github+json"},
    )
    if not isinstance(payload, list) or not payload:
        raise ValueError(f"No pull request found for head SHA {head_sha}.")

    candidates = sorted(
        payload,
        key=lambda pull: (
            str(pull.get("state") or "") == "open",
            str(pull.get("updated_at") or pull.get("created_at") or ""),
            int(pull.get("number") or 0),
        ),
        reverse=True,
    )
    selected = candidates[0]
    try:
        return _fetch_pull_request(
            client,
            owner=owner,
            repo=repo,
            pull_request_number=int(selected["number"]),
        )
    except GitHubApiError as exc:
        raise ValueError(
            f"Unable to fetch pull request details for head SHA {head_sha}: {exc}"
        ) from exc
