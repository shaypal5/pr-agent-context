from __future__ import annotations

from pr_agent_context.coverage.artifacts import (
    _list_run_artifacts,
    _safe_request_json,
    _select_coverage_source_run,
    discover_coverage_files,
)
from pr_agent_context.github.api import GitHubApiError


def test_discover_coverage_files_recurses_and_ignores_transient_files(tmp_path):
    coverage_root = tmp_path / "coverage-artifacts"
    nested = coverage_root / "linux" / "py312"
    nested.mkdir(parents=True)
    expected = nested / ".coverage.py312"
    expected.write_text("data", encoding="utf-8")
    (nested / ".coverage.py312-wal").write_text("ignore", encoding="utf-8")
    (nested / ".coverage.py312-shm").write_text("ignore", encoding="utf-8")

    discovered = discover_coverage_files(coverage_root)

    assert discovered == [expected]


class _ArtifactsClient:
    def __init__(self, responses):
        self.responses = responses
        self.calls = []

    def request_json(self, method, path, params=None):
        self.calls.append((method, path, params))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_safe_request_json_records_warning_for_api_errors():
    warnings = []
    client = _ArtifactsClient([GitHubApiError(502, "Bad Gateway", "oops")])

    payload = _safe_request_json(
        client,
        "GET",
        "/repos/shaypal5/example/actions/runs",
        params={"page": 1},
        warnings=warnings,
        warning_prefix="Unable to fetch runs",
    )

    assert payload is None
    assert warnings == ["Unable to fetch runs: GitHub API error 502: Bad Gateway"]


def test_list_run_artifacts_handles_pagination_and_empty_pages():
    client = _ArtifactsClient(
        [
            {"artifacts": [{"id": index} for index in range(100)]},
            {"artifacts": [{"id": 100}]},
        ]
    )

    artifacts = _list_run_artifacts(
        client,
        owner="shaypal5",
        repo="example",
        run_id=17,
        warnings=[],
    )

    assert len(artifacts) == 101

    empty_client = _ArtifactsClient([{"artifacts": []}])
    assert (
        _list_run_artifacts(empty_client, owner="shaypal5", repo="example", run_id=17, warnings=[])
        == []
    )


def test_select_coverage_source_run_keeps_first_accepted_run_and_records_later_matches():
    debug = {"candidate_runs": [], "warnings": []}
    client = _ArtifactsClient(
        [
            {
                "workflow_runs": [
                    {
                        "id": 2,
                        "name": "CI",
                        "conclusion": "success",
                        "updated_at": "2026-03-11T10:00:00Z",
                    },
                    {
                        "id": 1,
                        "name": "CI",
                        "conclusion": "success",
                        "updated_at": "2026-03-10T10:00:00Z",
                    },
                ]
            },
            {
                "artifacts": [
                    {"id": 200, "name": "pr-agent-context-coverage-py312", "size_in_bytes": 1}
                ]
            },
            {
                "artifacts": [
                    {"id": 100, "name": "pr-agent-context-coverage-py311", "size_in_bytes": 1}
                ]
            },
        ]
    )

    selected = _select_coverage_source_run(
        client,
        owner="shaypal5",
        repo="example",
        head_sha="deadbeef",
        artifact_prefix="pr-agent-context-coverage",
        workflow_names=(),
        allowed_conclusions=("success",),
        selection_strategy="latest_successful",
        max_candidate_runs=10,
        debug=debug,
    )

    assert selected["id"] == 2
    assert len(debug["candidate_runs"]) == 2
    assert all(candidate["accepted"] for candidate in debug["candidate_runs"])


def test_select_coverage_source_run_returns_none_when_runs_cannot_be_fetched():
    debug = {"candidate_runs": [], "warnings": []}
    client = _ArtifactsClient([GitHubApiError(503, "Service Unavailable", "offline")])

    selected = _select_coverage_source_run(
        client,
        owner="shaypal5",
        repo="example",
        head_sha="deadbeef",
        artifact_prefix="pr-agent-context-coverage",
        workflow_names=(),
        allowed_conclusions=("success",),
        selection_strategy="latest_successful",
        max_candidate_runs=10,
        debug=debug,
    )

    assert selected is None
    assert debug["warnings"]


def test_list_run_artifacts_returns_empty_when_pages_cannot_be_fetched():
    warnings = []
    client = _ArtifactsClient([GitHubApiError(502, "Bad Gateway", "oops")])

    assert (
        _list_run_artifacts(client, owner="shaypal5", repo="example", run_id=17, warnings=warnings)
        == []
    )
    assert warnings
