from __future__ import annotations

from io import BytesIO
from zipfile import ZipFile

from conftest import load_json_fixture, load_text_fixture
from pr_agent_context.domain.models import FailingCheck
from pr_agent_context.github import failing_checks as failing_checks_module
from pr_agent_context.github.api import GitHubApiError
from pr_agent_context.github.failing_checks import collect_failing_checks


def _zip_bytes(text: str) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("job.log", text)
    return buffer.getvalue()


class FakeFailingChecksClient:
    def __init__(self) -> None:
        self.workflow_runs = load_json_fixture("github/workflow_runs.json")
        self.check_runs = load_json_fixture("github/check_runs.json")
        self.commit_status = load_json_fixture("github/commit_status.json")
        self.workflow_jobs = {
            (201, 1): {
                "jobs": [
                    {
                        "id": 1101,
                        "name": "smoke (ubuntu-latest, 3.12)",
                        "workflow_name": "CI",
                        "conclusion": "failure",
                        "html_url": "https://github.com/shaypal5/example/actions/runs/201/job/1101",
                        "completed_at": "2026-03-08T12:00:00Z",
                        "steps": [{"name": "Run pytest", "conclusion": "failure"}],
                    }
                ]
            },
            (202, 2): {
                "jobs": [
                    {
                        "id": 1201,
                        "name": "smoke (ubuntu-latest, 3.12)",
                        "workflow_name": "CI",
                        "conclusion": "success",
                        "html_url": "https://github.com/shaypal5/example/actions/runs/202/job/1201",
                        "completed_at": "2026-03-08T12:10:00Z",
                        "steps": [{"name": "Run pytest", "conclusion": "success"}],
                    },
                    {
                        "id": 1202,
                        "name": "lint",
                        "workflow_name": "CI",
                        "conclusion": "failure",
                        "html_url": "https://github.com/shaypal5/example/actions/runs/202/job/1202",
                        "completed_at": "2026-03-08T12:11:00Z",
                        "steps": [{"name": "Run ruff", "conclusion": "failure"}],
                    },
                ]
            },
        }

    def request_json(self, method, path, params=None, payload=None, extra_headers=None):
        assert method == "GET"
        if path.endswith("/actions/runs"):
            return self.workflow_runs
        if "/actions/runs/" in path and path.endswith("/jobs"):
            parts = path.split("/")
            run_id = int(parts[-4])
            run_attempt = int(parts[-2])
            if (run_id, run_attempt) == (203, 1):
                raise GitHubApiError(404, "Not Found", "")
            return self.workflow_jobs[(run_id, run_attempt)]
        if path.endswith("/check-runs"):
            return self.check_runs
        if path.endswith("/status"):
            return self.commit_status
        raise AssertionError(f"Unexpected request_json call: {path}")

    def request_bytes(self, method, path, params=None, extra_headers=None):
        assert method == "GET"
        job_id = int(path.split("/")[-2])
        if job_id == 1101:
            return _zip_bytes(load_text_fixture("github/logs/pytest_failure.log"))
        if job_id == 1202:
            return _zip_bytes(load_text_fixture("github/logs/pre_commit_failure.log"))
        raise AssertionError(f"Unexpected request_bytes call: {path}")


class FakeCurrentRunClient:
    def request_json(self, method, path, params=None, payload=None, extra_headers=None):
        assert method == "GET"
        if "/actions/runs/" in path and path.endswith("/jobs"):
            return {
                "jobs": [
                    {
                        "id": 999,
                        "name": "smoke (ubuntu-latest, 3.12)",
                        "workflow_name": "CI",
                        "conclusion": "failure",
                        "html_url": "https://github.com/shaypal5/example/actions/runs/9/job/999",
                        "completed_at": "2026-03-08T13:00:00Z",
                        "steps": [{"name": "Run pytest", "conclusion": "failure"}],
                    }
                ]
            }
        raise AssertionError(f"Unexpected request_json call: {path}")

    def request_bytes(self, method, path, params=None, extra_headers=None):
        assert method == "GET"
        return _zip_bytes(load_text_fixture("github/logs/pytest_failure.log"))


class FakeForbiddenActionsRunsClient:
    def request_json(self, method, path, params=None, payload=None, extra_headers=None):
        assert method == "GET"
        if path.endswith("/actions/runs"):
            raise GitHubApiError(403, "Forbidden", "Resource not accessible by integration")
        raise AssertionError(f"Unexpected request_json call: {path}")

    def request_bytes(self, method, path, params=None, extra_headers=None):
        raise AssertionError(f"Unexpected request_bytes call: {path}")


class FakePaginatedChecksClient:
    def __init__(self) -> None:
        self.pages_seen: list[int] = []

    def request_json(self, method, path, params=None, payload=None, extra_headers=None):
        assert method == "GET"
        page = int((params or {}).get("page", 1))
        if path.endswith("/check-runs"):
            self.pages_seen.append(page)
            if page == 1:
                return {
                    "check_runs": [
                        {
                            "name": f"external-{index}",
                            "status": "completed",
                            "conclusion": "failure",
                            "details_url": f"https://example.invalid/check/{index}",
                            "app": {"slug": "custom-app"},
                        }
                        for index in range(100)
                    ]
                }
            return {"check_runs": []}
        raise AssertionError(f"Unexpected request_json call: {path}")


class FakePaginatedRunsClient:
    def __init__(self) -> None:
        self.pages_seen: list[int] = []

    def request_json(self, method, path, params=None, payload=None, extra_headers=None):
        assert method == "GET"
        page = int((params or {}).get("page", 1))
        if path.endswith("/actions/runs"):
            self.pages_seen.append(page)
            if page == 1:
                return {
                    "workflow_runs": [
                        {
                            "id": index,
                            "updated_at": "2026-03-08T12:00:00Z",
                            "created_at": "2026-03-08T12:00:00Z",
                        }
                        for index in range(100, 200)
                    ]
                }
            return {"workflow_runs": []}
        raise AssertionError(f"Unexpected request_json call: {path}")


class FakePaginatedJobsClient:
    def __init__(self) -> None:
        self.pages_seen: list[int] = []

    def request_json(self, method, path, params=None, payload=None, extra_headers=None):
        assert method == "GET"
        page = int((params or {}).get("page", 1))
        if path.endswith("/jobs"):
            self.pages_seen.append(page)
            if page == 1:
                return {"jobs": [{"id": index, "name": f"job-{index}"} for index in range(100)]}
            return {"jobs": [{"id": 999, "name": "job-999"}]}
        raise AssertionError(f"Unexpected request_json call: {path}")


class FakeForbiddenChecksClient:
    def request_json(self, method, path, params=None, payload=None, extra_headers=None):
        assert method == "GET"
        if path.endswith("/check-runs"):
            raise GitHubApiError(403, "Forbidden", "Resource not accessible by integration")
        raise AssertionError(f"Unexpected request_json call: {path}")


class FakeForbiddenStatusesClient:
    def request_json(self, method, path, params=None, payload=None, extra_headers=None):
        assert method == "GET"
        if path.endswith("/status"):
            raise GitHubApiError(403, "Forbidden", "Resource not accessible by integration")
        raise AssertionError(f"Unexpected request_json call: {path}")


class FakeBrokenLogsClient:
    def request_bytes(self, method, path, params=None, extra_headers=None):
        assert method == "GET"
        raise GitHubApiError(502, "Bad Gateway", "log download failed")


def test_collect_failing_checks_aggregates_head_sha_failures():
    failures, debug = collect_failing_checks(
        FakeFailingChecksClient(),
        owner="shaypal5",
        repo="example",
        head_sha="def456",
        current_run_id=202,
        current_run_attempt=2,
        include_cross_run_failures=True,
        include_external_checks=True,
        max_actions_runs=10,
        max_actions_jobs=10,
        max_external_checks=10,
        max_failing_checks=10,
        max_log_lines_per_job=6,
    )

    assert [(failure.source_type, failure.job_name) for failure in failures] == [
        ("actions_job", "lint"),
        ("actions_workflow_run", "release workflow"),
        ("external_check_run", "codecov/patch"),
        ("commit_status", "security/scan"),
    ]
    assert failures[0].is_current_run is True
    assert failures[0].failed_steps == ["Run ruff"]
    assert failures[0].logs_available is True
    assert failures[1].summary == "Workflow run failed, but job details were unavailable."
    assert failures[2].app_name == "codecov"
    assert failures[3].context_name == "security/scan"
    assert debug["deduped_source_counts"] == {
        "actions_job": 1,
        "actions_workflow_run": 1,
        "commit_status": 1,
        "external_check_run": 1,
    }
    assert not any(failure["job_name"] == "smoke" for failure in debug["deduped_failures"])


def test_collect_failing_checks_can_stay_current_run_only():
    failures, debug = collect_failing_checks(
        FakeCurrentRunClient(),
        owner="shaypal5",
        repo="example",
        head_sha="def456",
        current_run_id=9,
        current_run_attempt=1,
        include_cross_run_failures=False,
        include_external_checks=False,
        max_actions_runs=10,
        max_actions_jobs=10,
        max_external_checks=10,
        max_failing_checks=10,
        max_log_lines_per_job=6,
    )

    assert len(failures) == 1
    failure = failures[0]
    assert failure.source_type == "actions_job"
    assert failure.job_name == "smoke"
    assert failure.is_current_run is True
    assert debug["deduped_source_counts"] == {"actions_job": 1}


def test_collect_failing_checks_degrades_gracefully_when_actions_runs_are_forbidden():
    failures, debug = collect_failing_checks(
        FakeForbiddenActionsRunsClient(),
        owner="shaypal5",
        repo="example",
        head_sha="def456",
        current_run_id=9,
        current_run_attempt=1,
        include_cross_run_failures=True,
        include_external_checks=False,
        max_actions_runs=10,
        max_actions_jobs=10,
        max_external_checks=10,
        max_failing_checks=10,
        max_log_lines_per_job=6,
    )

    assert failures == []
    assert debug["deduped_source_counts"] == {}
    assert any(
        warning.startswith("Unable to fetch workflow runs for head SHA def456:")
        for warning in debug["warnings"]
    )


def test_collect_external_check_runs_handles_pagination():
    client = FakePaginatedChecksClient()

    failures, warnings = failing_checks_module._collect_external_check_runs(
        client,
        owner="shaypal5",
        repo="example",
        head_sha="def456",
        max_external_checks=150,
    )

    assert not warnings
    assert client.pages_seen == [1, 2]
    assert len(failures) == 100


def test_collect_external_check_runs_degrades_gracefully_on_forbidden():
    failures, warnings = failing_checks_module._collect_external_check_runs(
        FakeForbiddenChecksClient(),
        owner="shaypal5",
        repo="example",
        head_sha="def456",
        max_external_checks=10,
    )

    assert failures == []
    assert warnings == ["Unable to fetch check runs: GitHub API error 403: Forbidden"]


def test_collect_commit_status_failures_degrades_gracefully_on_forbidden():
    failures, warnings = failing_checks_module._collect_commit_status_failures(
        FakeForbiddenStatusesClient(),
        owner="shaypal5",
        repo="example",
        head_sha="def456",
        max_external_checks=10,
        existing_names=set(),
    )

    assert failures == []
    assert warnings == ["Unable to fetch commit statuses: GitHub API error 403: Forbidden"]


def test_fetch_workflow_runs_for_head_sha_handles_pagination_and_sorting():
    client = FakePaginatedRunsClient()

    runs = failing_checks_module._fetch_workflow_runs_for_head_sha(
        client,
        owner="shaypal5",
        repo="example",
        head_sha="def456",
        max_runs=150,
        warnings=[],
    )

    assert client.pages_seen == [1, 2]
    assert len(runs) == 100
    assert runs[0]["id"] == 199
    assert runs[-1]["id"] == 100


def test_fetch_run_jobs_handles_pagination_and_attaches_run_stub():
    client = FakePaginatedJobsClient()

    jobs = failing_checks_module._fetch_run_jobs(
        client,
        owner="shaypal5",
        repo="example",
        run_id=321,
        run_attempt=2,
        warnings=[],
    )

    assert client.pages_seen == [1, 2]
    assert len(jobs) == 101
    assert jobs[0]["__run__"]["id"] == 321
    assert jobs[-1]["__run__"]["run_attempt"] == 2


def test_normalize_actions_job_handles_log_download_errors():
    failure = failing_checks_module._normalize_actions_job(
        FakeBrokenLogsClient(),
        owner="shaypal5",
        repo="example",
        head_sha="def456",
        run={"id": 9, "run_attempt": 1, "run_number": 4, "name": "CI", "html_url": ""},
        raw_job={
            "id": 77,
            "name": "lint",
            "workflow_name": "CI",
            "conclusion": "failure",
            "html_url": "",
            "steps": [{"name": "Run ruff", "conclusion": "failure"}],
        },
        current_run_id=9,
        current_run_attempt=1,
        max_log_lines_per_job=6,
    )

    assert failure.logs_available is False
    assert failure.excerpt_lines == []
    assert failure.url == ""


def test_fallback_dedupe_key_and_external_check_status_filters():
    failure = FailingCheck.model_validate(
        {
            "source_type": "external_check_run",
            "workflow_name": "custom-app",
            "job_name": "check-1",
            "matrix_label": "linux",
            "app_name": "custom-app",
            "url": "",
        }
    )

    assert failing_checks_module._fallback_dedupe_key(failure) == (
        "external_check_run::custom-app::check-1::linux::custom-app"
    )
    assert (
        failing_checks_module._is_relevant_external_check_run(
            {
                "app": {"slug": "custom-app"},
                "status": "queued",
                "conclusion": "failure",
            }
        )
        is False
    )
