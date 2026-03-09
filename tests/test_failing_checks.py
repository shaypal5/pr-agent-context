from __future__ import annotations

from io import BytesIO
from zipfile import ZipFile

from conftest import load_json_fixture, load_text_fixture
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
