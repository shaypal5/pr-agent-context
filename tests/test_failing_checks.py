from __future__ import annotations

from datetime import datetime, timezone
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
        if path.endswith("/jobs"):
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


class FakeMixedOutcomeActionsClient:
    def request_json(self, method, path, params=None, payload=None, extra_headers=None):
        assert method == "GET"
        if path.endswith("/actions/runs"):
            return {
                "workflow_runs": [
                    {
                        "id": 20,
                        "run_attempt": 1,
                        "run_number": 2,
                        "name": "CI",
                        "display_title": "lint workflow",
                        "conclusion": "failure",
                        "updated_at": "2026-03-08T12:10:00Z",
                        "created_at": "2026-03-08T12:10:00Z",
                        "html_url": "https://example.invalid/runs/20",
                    },
                    {
                        "id": 19,
                        "run_attempt": 1,
                        "run_number": 1,
                        "name": "CI",
                        "display_title": "lint workflow",
                        "conclusion": "success",
                        "updated_at": "2026-03-08T12:00:00Z",
                        "created_at": "2026-03-08T12:00:00Z",
                        "html_url": "https://example.invalid/runs/19",
                    },
                ]
            }
        if "/actions/runs/" in path and path.endswith("/jobs"):
            if "/actions/runs/20/" in path:
                return {
                    "jobs": [
                        {
                            "id": 2001,
                            "name": "lint",
                            "workflow_name": "CI",
                            "conclusion": "failure",
                            "html_url": "https://example.invalid/jobs/2001",
                            "completed_at": "2026-03-08T12:10:00Z",
                            "steps": [{"name": "Run ruff", "conclusion": "failure"}],
                        }
                    ]
                }
            return {
                "jobs": [
                    {
                        "id": 1901,
                        "name": "lint",
                        "workflow_name": "CI",
                        "conclusion": "success",
                        "html_url": "https://example.invalid/jobs/1901",
                        "completed_at": "2026-03-08T12:00:00Z",
                        "steps": [{"name": "Run ruff", "conclusion": "success"}],
                    }
                ]
            }
        raise AssertionError(f"Unexpected request_json call: {path}")

    def request_bytes(self, method, path, params=None, extra_headers=None):
        assert method == "GET"
        return _zip_bytes(load_text_fixture("github/logs/pre_commit_failure.log"))


class FakeActionRequiredRunClient:
    def request_json(self, method, path, params=None, payload=None, extra_headers=None):
        assert method == "GET"
        if path.endswith("/actions/runs"):
            return {
                "workflow_runs": [
                    {
                        "id": 203,
                        "run_attempt": 1,
                        "run_number": 7,
                        "name": "release workflow",
                        "display_title": "release workflow",
                        "conclusion": "action_required",
                        "updated_at": "2026-03-08T12:12:00Z",
                        "created_at": "2026-03-08T12:12:00Z",
                        "html_url": "https://example.invalid/runs/203",
                    }
                ]
            }
        if "/actions/runs/" in path and path.endswith("/jobs"):
            raise GitHubApiError(404, "Not Found", "")
        raise AssertionError(f"Unexpected request_json call: {path}")

    def request_bytes(self, method, path, params=None, extra_headers=None):
        raise AssertionError(f"Unexpected request_bytes call: {path}")


class FakeSettlingChecksClient:
    def __init__(self) -> None:
        self.check_run_poll_count = 0
        self.status_poll_count = 0
        self.run_poll_count = 0

    def request_json(self, method, path, params=None, payload=None, extra_headers=None):
        assert method == "GET"
        if path.endswith("/actions/runs"):
            self.run_poll_count += 1
            return {"workflow_runs": []}
        if path.endswith("/jobs"):
            return {"jobs": []}
        if path.endswith("/check-runs"):
            self.check_run_poll_count += 1
            if self.check_run_poll_count < 3:
                return {"check_runs": []}
            return {
                "check_runs": [
                    {
                        "name": "codecov/patch",
                        "status": "completed",
                        "conclusion": "failure",
                        "details_url": "https://example.invalid/codecov/patch",
                        "app": {"slug": "codecov"},
                        "completed_at": "2026-03-08T12:00:00Z",
                        "output": {
                            "title": "Patch coverage failed",
                            "summary": "Coverage dropped below threshold.",
                        },
                    }
                ]
            }
        if path.endswith("/status"):
            self.status_poll_count += 1
            return {"statuses": []}
        raise AssertionError(f"Unexpected request_json call: {path}")

    def request_bytes(self, method, path, params=None, extra_headers=None):
        raise AssertionError(f"Unexpected request_bytes call: {path}")


class FakeNeverSettledChecksClient:
    def __init__(self) -> None:
        self.check_run_poll_count = 0

    def request_json(self, method, path, params=None, payload=None, extra_headers=None):
        assert method == "GET"
        if path.endswith("/actions/runs"):
            return {"workflow_runs": []}
        if path.endswith("/jobs"):
            return {"jobs": []}
        if path.endswith("/check-runs"):
            self.check_run_poll_count += 1
            return {
                "check_runs": [
                    {
                        "name": "codecov/patch",
                        "status": "in_progress",
                        "conclusion": None,
                        "details_url": "https://example.invalid/codecov/patch",
                        "app": {"slug": "codecov"},
                    }
                ]
            }
        if path.endswith("/status"):
            return {"statuses": []}
        raise AssertionError(f"Unexpected request_json call: {path}")

    def request_bytes(self, method, path, params=None, extra_headers=None):
        raise AssertionError(f"Unexpected request_bytes call: {path}")


class FakeTimedOutCurrentRunFailuresClient:
    def __init__(self) -> None:
        self.check_run_poll_count = 0

    def request_json(self, method, path, params=None, payload=None, extra_headers=None):
        assert method == "GET"
        if path.endswith("/actions/runs"):
            return {
                "workflow_runs": [
                    {
                        "id": 9,
                        "run_attempt": 1,
                        "run_number": 17,
                        "name": "CI",
                        "display_title": "pull request checks",
                        "status": "in_progress",
                        "conclusion": None,
                        "updated_at": "2026-03-19T09:45:00Z",
                        "created_at": "2026-03-19T09:44:00Z",
                    }
                ]
            }
        if "/actions/runs/" in path and path.endswith("/jobs"):
            return {
                "jobs": [
                    {
                        "id": 901,
                        "name": "smoke (ubuntu-latest, 3.12)",
                        "workflow_name": "CI",
                        "conclusion": "failure",
                        "html_url": "https://example.invalid/runs/9/jobs/901",
                        "completed_at": "2026-03-19T09:44:20Z",
                        "steps": [{"name": "Run pytest", "conclusion": "failure"}],
                    },
                    {
                        "id": 902,
                        "name": "pr-agent-context",
                        "workflow_name": "CI",
                        "conclusion": None,
                        "html_url": "https://example.invalid/runs/9/jobs/902",
                        "completed_at": None,
                        "steps": [],
                    },
                ]
            }
        if path.endswith("/check-runs"):
            self.check_run_poll_count += 1
            return {"check_runs": []}
        if path.endswith("/status"):
            return {"statuses": []}
        raise AssertionError(f"Unexpected request_json call: {path}")

    def request_bytes(self, method, path, params=None, extra_headers=None):
        assert method == "GET"
        if path.endswith("/901/logs"):
            return _zip_bytes(load_text_fixture("github/logs/pytest_failure.log"))
        raise AssertionError(f"Unexpected request_bytes call: {path}")


class FakeSettlementWarningClient:
    def request_json(self, method, path, params=None, payload=None, extra_headers=None):
        assert method == "GET"
        if path.endswith("/actions/runs"):
            raise GitHubApiError(403, "Forbidden", "no actions")
        if path.endswith("/check-runs"):
            raise GitHubApiError(403, "Forbidden", "no checks")
        if path.endswith("/status"):
            return {"statuses": [{"context": "codecov/patch", "state": "pending"}]}
        raise AssertionError(f"Unexpected request_json call: {path}")

    def request_bytes(self, method, path, params=None, extra_headers=None):
        raise AssertionError(f"Unexpected request_bytes call: {path}")


class FakeSettlementPaginationClient:
    def __init__(self) -> None:
        self.check_run_pages: list[int] = []

    def request_json(self, method, path, params=None, payload=None, extra_headers=None):
        assert method == "GET"
        page = int((params or {}).get("page", 1))
        if path.endswith("/check-runs"):
            self.check_run_pages.append(page)
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
        if path.endswith("/status"):
            raise GitHubApiError(403, "Forbidden", "no statuses")
        if path.endswith("/actions/runs"):
            return {"workflow_runs": []}
        raise AssertionError(f"Unexpected request_json call: {path}")

    def request_bytes(self, method, path, params=None, extra_headers=None):
        raise AssertionError(f"Unexpected request_bytes call: {path}")


class FakeNoPollClient:
    def request_json(self, method, path, params=None, payload=None, extra_headers=None):
        if path.endswith("/jobs"):
            return {"jobs": []}
        raise AssertionError(f"Unexpected request_json call: {path}")

    def request_bytes(self, method, path, params=None, extra_headers=None):
        raise AssertionError(f"Unexpected request_bytes call: {path}")


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
        wait_for_checks_to_settle=False,
        max_actions_runs=10,
        max_actions_jobs=10,
        max_external_checks=10,
        max_failing_checks=10,
        max_log_lines_per_job=6,
        check_settle_timeout_seconds=45,
        check_settle_poll_interval_seconds=5,
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
        wait_for_checks_to_settle=False,
        max_actions_runs=10,
        max_actions_jobs=10,
        max_external_checks=10,
        max_failing_checks=10,
        max_log_lines_per_job=6,
        check_settle_timeout_seconds=45,
        check_settle_poll_interval_seconds=5,
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
        wait_for_checks_to_settle=False,
        max_actions_runs=10,
        max_actions_jobs=10,
        max_external_checks=10,
        max_failing_checks=10,
        max_log_lines_per_job=6,
        check_settle_timeout_seconds=45,
        check_settle_poll_interval_seconds=5,
    )

    assert failures == []
    assert debug["deduped_source_counts"] == {}
    assert any(
        warning.startswith("Unable to fetch workflow runs for head SHA def456:")
        for warning in debug["warnings"]
    )


def test_collect_failing_checks_keeps_failed_actions_observation_over_successful_rerun():
    failures, _ = collect_failing_checks(
        FakeMixedOutcomeActionsClient(),
        owner="shaypal5",
        repo="example",
        head_sha="def456",
        current_run_id=19,
        current_run_attempt=1,
        include_cross_run_failures=True,
        include_external_checks=False,
        wait_for_checks_to_settle=False,
        max_actions_runs=10,
        max_actions_jobs=10,
        max_external_checks=10,
        max_failing_checks=10,
        max_log_lines_per_job=6,
        check_settle_timeout_seconds=45,
        check_settle_poll_interval_seconds=5,
    )

    assert len(failures) == 1
    assert failures[0].source_type == "actions_job"
    assert failures[0].conclusion == "failure"
    assert failures[0].job_id == 2001


def test_collect_failing_checks_includes_action_required_run_level_failures():
    failures, _ = collect_failing_checks(
        FakeActionRequiredRunClient(),
        owner="shaypal5",
        repo="example",
        head_sha="def456",
        current_run_id=1,
        current_run_attempt=1,
        include_cross_run_failures=True,
        include_external_checks=False,
        wait_for_checks_to_settle=False,
        max_actions_runs=10,
        max_actions_jobs=10,
        max_external_checks=10,
        max_failing_checks=10,
        max_log_lines_per_job=6,
        check_settle_timeout_seconds=45,
        check_settle_poll_interval_seconds=5,
    )


def test_collect_failing_checks_waits_for_late_external_checks(monkeypatch):
    client = FakeSettlingChecksClient()
    monotonic_values = iter([0.0, 0.1, 1.0, 1.1, 2.0, 2.1, 3.0, 3.1, 4.0])

    monkeypatch.setattr(failing_checks_module, "_sleep", lambda _: None)
    monkeypatch.setattr(failing_checks_module, "_monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(
        failing_checks_module,
        "_minimum_check_settle_wait_seconds",
        lambda **_: 2,
    )

    failures, debug = collect_failing_checks(
        client,
        owner="shaypal5",
        repo="example",
        head_sha="def456",
        current_run_id=1,
        current_run_attempt=1,
        include_cross_run_failures=True,
        include_external_checks=True,
        wait_for_checks_to_settle=True,
        max_actions_runs=10,
        max_actions_jobs=10,
        max_external_checks=10,
        max_failing_checks=10,
        max_log_lines_per_job=6,
        check_settle_timeout_seconds=10,
        check_settle_poll_interval_seconds=1,
    )

    assert [failure.job_name for failure in failures] == ["codecov/patch"]
    assert debug["settlement"]["enabled"] is True
    assert debug["settlement"]["settled"] is True
    assert debug["settlement"]["timed_out"] is False
    assert debug["settlement"]["poll_count"] >= 4
    assert client.check_run_poll_count >= 5


def test_collect_failing_checks_times_out_when_check_universe_never_settles(monkeypatch):
    client = FakeNeverSettledChecksClient()
    monotonic_values = iter([0.0, 0.1, 1.0, 1.1, 2.0, 2.1, 3.1, 3.2])

    monkeypatch.setattr(failing_checks_module, "_sleep", lambda _: None)
    monkeypatch.setattr(failing_checks_module, "_monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(
        failing_checks_module,
        "_minimum_check_settle_wait_seconds",
        lambda **_: 1,
    )

    failures, debug = collect_failing_checks(
        client,
        owner="shaypal5",
        repo="example",
        head_sha="def456",
        current_run_id=1,
        current_run_attempt=1,
        include_cross_run_failures=True,
        include_external_checks=True,
        wait_for_checks_to_settle=True,
        max_actions_runs=10,
        max_actions_jobs=10,
        max_external_checks=10,
        max_failing_checks=10,
        max_log_lines_per_job=6,
        check_settle_timeout_seconds=3,
        check_settle_poll_interval_seconds=1,
    )

    assert failures == []
    assert debug["settlement"]["enabled"] is True
    assert debug["settlement"]["settled"] is False
    assert debug["settlement"]["timed_out"] is True
    assert debug["settlement"]["pending_count"] == 1
    assert client.check_run_poll_count >= 3


def test_collect_failing_checks_preserves_current_run_failures_after_timeout(monkeypatch):
    client = FakeTimedOutCurrentRunFailuresClient()
    monotonic_values = iter([0.0, 0.1, 1.0, 1.1, 2.0, 2.1, 3.1, 3.2])

    monkeypatch.setattr(failing_checks_module, "_sleep", lambda _: None)
    monkeypatch.setattr(failing_checks_module, "_monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(
        failing_checks_module,
        "_minimum_check_settle_wait_seconds",
        lambda **_: 1,
    )

    failures, debug = collect_failing_checks(
        client,
        owner="shaypal5",
        repo="example",
        head_sha="def456",
        current_run_id=9,
        current_run_attempt=1,
        include_cross_run_failures=True,
        include_external_checks=True,
        wait_for_checks_to_settle=True,
        max_actions_runs=10,
        max_actions_jobs=10,
        max_external_checks=10,
        max_failing_checks=10,
        max_log_lines_per_job=6,
        check_settle_timeout_seconds=3,
        check_settle_poll_interval_seconds=1,
    )

    assert [failure.job_name for failure in failures] == ["smoke"]
    assert failures[0].matrix_label == "ubuntu-latest, 3.12"
    assert failures[0].is_current_run is True
    assert debug["settlement"]["timed_out"] is True
    assert debug["settlement"]["failures_observed_after_timeout"] is True


def test_collect_failing_checks_skips_settlement_when_timeout_non_positive():
    failures, debug = collect_failing_checks(
        FakeNoPollClient(),
        owner="shaypal5",
        repo="example",
        head_sha="def456",
        current_run_id=1,
        current_run_attempt=1,
        include_cross_run_failures=False,
        include_external_checks=False,
        wait_for_checks_to_settle=True,
        max_actions_runs=10,
        max_actions_jobs=10,
        max_external_checks=10,
        max_failing_checks=10,
        max_log_lines_per_job=6,
        check_settle_timeout_seconds=0,
        check_settle_poll_interval_seconds=5,
    )

    assert failures == []
    assert debug["settlement"]["enabled"] is True
    assert debug["settlement"]["settled"] is False
    assert debug["settlement"]["timed_out"] is False
    assert debug["settlement"]["skipped_reason"] == "timeout_non_positive"


def test_collect_failing_checks_skips_settlement_when_poll_interval_non_positive():
    failures, debug = collect_failing_checks(
        FakeNoPollClient(),
        owner="shaypal5",
        repo="example",
        head_sha="def456",
        current_run_id=1,
        current_run_attempt=1,
        include_cross_run_failures=False,
        include_external_checks=False,
        wait_for_checks_to_settle=True,
        max_actions_runs=10,
        max_actions_jobs=10,
        max_external_checks=10,
        max_failing_checks=10,
        max_log_lines_per_job=6,
        check_settle_timeout_seconds=30,
        check_settle_poll_interval_seconds=0,
    )

    assert failures == []
    assert debug["settlement"]["enabled"] is True
    assert debug["settlement"]["settled"] is False
    assert debug["settlement"]["timed_out"] is False
    assert debug["settlement"]["skipped_reason"] == "poll_interval_non_positive"


def test_wait_for_check_settlement_caps_sleep_to_remaining_time(monkeypatch):
    client = FakeNeverSettledChecksClient()
    monotonic_values = iter([0.0, 2.5, 3.0])
    sleeps: list[float] = []

    monkeypatch.setattr(failing_checks_module, "_monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(failing_checks_module, "_sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(
        failing_checks_module,
        "_minimum_check_settle_wait_seconds",
        lambda **_: 1,
    )

    settlement, _ = failing_checks_module._wait_for_check_settlement(
        client,
        owner="shaypal5",
        repo="example",
        head_sha="def456",
        include_cross_run_failures=False,
        include_external_checks=True,
        max_actions_runs=10,
        max_external_checks=10,
        timeout_seconds=3,
        poll_interval_seconds=5,
    )

    assert settlement["timed_out"] is True
    assert sleeps == [0.5]


def test_wait_for_check_settlement_deduplicates_repeated_warnings(monkeypatch):
    client = FakeSettlementWarningClient()
    monotonic_values = iter([0.0, 0.1, 1.0, 1.1, 2.0, 2.1, 3.1, 3.2])

    monkeypatch.setattr(failing_checks_module, "_sleep", lambda _: None)
    monkeypatch.setattr(failing_checks_module, "_monotonic", lambda: next(monotonic_values))
    monkeypatch.setattr(
        failing_checks_module,
        "_minimum_check_settle_wait_seconds",
        lambda **_: 1,
    )

    settlement, warnings = failing_checks_module._wait_for_check_settlement(
        client,
        owner="shaypal5",
        repo="example",
        head_sha="def456",
        include_cross_run_failures=True,
        include_external_checks=True,
        max_actions_runs=10,
        max_external_checks=10,
        timeout_seconds=3,
        poll_interval_seconds=1,
    )

    assert settlement["timed_out"] is True
    assert settlement["pending_source_counts"] == {"commit_statuses": 1}
    assert settlement["warnings"] == warnings
    assert (
        warnings.count(
            "Unable to fetch workflow runs for head SHA def456: GitHub API error 403: Forbidden"
        )
        == 1
    )
    assert warnings.count("Unable to poll check runs: GitHub API error 403: Forbidden") == 1


def test_collect_check_settlement_snapshot_counts_pending_commit_statuses():
    snapshot, warnings = failing_checks_module._collect_check_settlement_snapshot(
        FakeSettlementWarningClient(),
        owner="shaypal5",
        repo="example",
        head_sha="def456",
        include_cross_run_failures=False,
        include_external_checks=True,
        max_actions_runs=10,
        max_external_checks=10,
    )

    assert snapshot["pending_count"] == 1
    assert snapshot["pending_source_counts"] == {"commit_statuses": 1}
    assert warnings == ["Unable to poll check runs: GitHub API error 403: Forbidden"]


def test_minimum_check_settle_wait_seconds_handles_zero_timeout():
    assert (
        failing_checks_module._minimum_check_settle_wait_seconds(
            timeout_seconds=0,
            poll_interval_seconds=5,
        )
        == 0
    )


def test_fetch_external_check_run_snapshots_handles_empty_payload():
    snapshots = failing_checks_module._fetch_external_check_run_snapshots(
        FakeForbiddenChecksClient(),
        owner="shaypal5",
        repo="example",
        head_sha="def456",
        max_external_checks=10,
        warnings=[],
    )

    assert snapshots == []


def test_fetch_external_check_run_snapshots_handles_pagination():
    client = FakeSettlementPaginationClient()

    snapshots = failing_checks_module._fetch_external_check_run_snapshots(
        client,
        owner="shaypal5",
        repo="example",
        head_sha="def456",
        max_external_checks=150,
        warnings=[],
    )

    assert client.check_run_pages == [1, 2]
    assert len(snapshots) == 100


def test_fetch_commit_status_snapshots_handles_missing_payload():
    snapshots = failing_checks_module._fetch_commit_status_snapshots(
        FakeForbiddenStatusesClient(),
        owner="shaypal5",
        repo="example",
        head_sha="def456",
        max_external_checks=10,
        warnings=[],
    )

    assert snapshots == []


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


def test_collect_failing_checks_suppresses_codecov_external_signals():
    failures, debug = collect_failing_checks(
        FakeFailingChecksClient(),
        owner="shaypal5",
        repo="example",
        head_sha="def456",
        current_run_id=202,
        current_run_attempt=2,
        include_cross_run_failures=False,
        include_external_checks=True,
        wait_for_checks_to_settle=False,
        max_actions_runs=20,
        max_actions_jobs=20,
        max_external_checks=20,
        max_failing_checks=20,
        max_log_lines_per_job=20,
        check_settle_timeout_seconds=0,
        check_settle_poll_interval_seconds=0,
        suppress_codecov_checks=True,
    )

    assert all(failure.job_name != "codecov/patch" for failure in failures)
    assert all(failure.context_name != "codecov/patch" for failure in failures)
    assert any(failure.job_name == "security/scan" for failure in failures)
    assert debug["deduped_source_counts"]["commit_status"] == 1


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


class FakeCompletedActionsRunsClient:
    def request_json(self, method, path, params=None, payload=None, extra_headers=None):
        assert method == "GET"
        if path.endswith("/actions/runs"):
            return {
                "workflow_runs": [
                    {
                        "id": 1,
                        "name": "CI",
                        "status": "completed",
                        "conclusion": "success",
                        "updated_at": "2026-03-08T12:00:00Z",
                        "created_at": "2026-03-08T12:00:00Z",
                    }
                ]
            }
        raise AssertionError(f"Unexpected request_json call: {path}")


class FakeSuccessfulRunWithoutJobsClient:
    def request_json(self, method, path, params=None, payload=None, extra_headers=None):
        assert method == "GET"
        if path.endswith("/actions/runs"):
            return {
                "workflow_runs": [
                    {
                        "id": 1,
                        "run_attempt": 1,
                        "conclusion": "success",
                        "updated_at": "2026-03-08T12:00:00Z",
                        "created_at": "2026-03-08T12:00:00Z",
                    }
                ]
            }
        if path.endswith("/jobs"):
            return {"jobs": []}
        raise AssertionError(f"Unexpected request_json call: {path}")


def test_wait_for_check_settlement_repolls_without_sleep_when_poll_interval_is_zero(monkeypatch):
    snapshots = iter(
        [
            (
                {
                    "fingerprint": ["pending"],
                    "pending_count": 1,
                    "snapshot_count": 1,
                    "actions_run_count": 0,
                    "external_check_run_count": 0,
                    "commit_status_count": 0,
                    "pending_source_counts": {},
                },
                [],
            ),
            (
                {
                    "fingerprint": ["stable"],
                    "pending_count": 0,
                    "snapshot_count": 1,
                    "actions_run_count": 0,
                    "external_check_run_count": 0,
                    "commit_status_count": 0,
                    "pending_source_counts": {},
                },
                [],
            ),
            (
                {
                    "fingerprint": ["stable"],
                    "pending_count": 0,
                    "snapshot_count": 1,
                    "actions_run_count": 0,
                    "external_check_run_count": 0,
                    "commit_status_count": 0,
                    "pending_source_counts": {},
                },
                [],
            ),
        ]
    )
    monkeypatch.setattr(
        failing_checks_module,
        "_collect_check_settlement_snapshot",
        lambda *args, **kwargs: next(snapshots),
    )
    monkeypatch.setattr(failing_checks_module, "_minimum_check_settle_wait_seconds", lambda **_: 0)
    monkeypatch.setattr(failing_checks_module, "_monotonic", iter([0.0, 0.1, 0.2, 0.3]).__next__)
    monkeypatch.setattr(
        failing_checks_module,
        "_sleep",
        lambda _: (_ for _ in ()).throw(AssertionError("sleep should not be called")),
    )

    settlement, warnings = failing_checks_module._wait_for_check_settlement(
        FakeNoPollClient(),
        owner="shaypal5",
        repo="example",
        head_sha="def456",
        include_cross_run_failures=False,
        include_external_checks=False,
        max_actions_runs=10,
        max_external_checks=10,
        timeout_seconds=5,
        poll_interval_seconds=0,
    )

    assert settlement["settled"] is True
    assert warnings == []


def test_wait_for_check_settlement_repolls_without_sleep_when_timeout_is_zero(monkeypatch):
    snapshots = iter(
        [
            (
                {
                    "fingerprint": ["pending"],
                    "pending_count": 1,
                    "snapshot_count": 1,
                    "actions_run_count": 0,
                    "external_check_run_count": 0,
                    "commit_status_count": 0,
                    "pending_source_counts": {},
                },
                [],
            ),
            (
                {
                    "fingerprint": ["stable"],
                    "pending_count": 0,
                    "snapshot_count": 1,
                    "actions_run_count": 0,
                    "external_check_run_count": 0,
                    "commit_status_count": 0,
                    "pending_source_counts": {},
                },
                [],
            ),
            (
                {
                    "fingerprint": ["stable"],
                    "pending_count": 0,
                    "snapshot_count": 1,
                    "actions_run_count": 0,
                    "external_check_run_count": 0,
                    "commit_status_count": 0,
                    "pending_source_counts": {},
                },
                [],
            ),
        ]
    )
    monkeypatch.setattr(
        failing_checks_module,
        "_collect_check_settlement_snapshot",
        lambda *args, **kwargs: next(snapshots),
    )
    monkeypatch.setattr(failing_checks_module, "_minimum_check_settle_wait_seconds", lambda **_: 0)
    monkeypatch.setattr(failing_checks_module, "_monotonic", iter([0.0, 0.1, 0.2, 0.3]).__next__)
    monkeypatch.setattr(
        failing_checks_module,
        "_sleep",
        lambda _: (_ for _ in ()).throw(AssertionError("sleep should not be called")),
    )

    settlement, warnings = failing_checks_module._wait_for_check_settlement(
        FakeNoPollClient(),
        owner="shaypal5",
        repo="example",
        head_sha="def456",
        include_cross_run_failures=False,
        include_external_checks=False,
        max_actions_runs=10,
        max_external_checks=10,
        timeout_seconds=0,
        poll_interval_seconds=5,
    )

    assert settlement["settled"] is True
    assert warnings == []


def test_collect_check_settlement_snapshot_handles_completed_actions_without_external_checks():
    snapshot, warnings = failing_checks_module._collect_check_settlement_snapshot(
        FakeCompletedActionsRunsClient(),
        owner="shaypal5",
        repo="example",
        head_sha="def456",
        include_cross_run_failures=True,
        include_external_checks=False,
        max_actions_runs=10,
        max_external_checks=10,
    )

    assert snapshot["actions_run_count"] == 1
    assert snapshot["pending_source_counts"] == {}
    assert warnings == []


def test_collect_actions_failures_for_head_sha_ignores_successful_runs_without_jobs():
    failures, warnings = failing_checks_module._collect_actions_failures_for_head_sha(
        FakeSuccessfulRunWithoutJobsClient(),
        owner="shaypal5",
        repo="example",
        head_sha="def456",
        current_run_id=1,
        current_run_attempt=1,
        max_actions_runs=10,
        max_actions_jobs=10,
        max_log_lines_per_job=6,
    )

    assert failures == []
    assert warnings == []


def test_collect_external_check_runs_returns_empty_when_limit_is_zero():
    failures, warnings = failing_checks_module._collect_external_check_runs(
        FakePaginatedChecksClient(),
        owner="shaypal5",
        repo="example",
        head_sha="def456",
        max_external_checks=0,
    )

    assert failures == []
    assert warnings == []


def test_fetch_workflow_runs_for_head_sha_returns_empty_when_limit_is_zero():
    runs = failing_checks_module._fetch_workflow_runs_for_head_sha(
        FakePaginatedRunsClient(),
        owner="shaypal5",
        repo="example",
        head_sha="def456",
        max_runs=0,
        warnings=[],
    )

    assert runs == []


def test_fetch_external_check_run_snapshots_returns_empty_when_limit_is_zero():
    snapshots = failing_checks_module._fetch_external_check_run_snapshots(
        FakePaginatedChecksClient(),
        owner="shaypal5",
        repo="example",
        head_sha="def456",
        max_external_checks=0,
        warnings=[],
    )

    assert snapshots == []


def test_selection_score_includes_summary_bonus():
    without_summary = FailingCheck.model_validate(
        {
            "source_type": "actions_job",
            "conclusion": "failure",
            "job_name": "lint",
            "workflow_name": "CI",
            "url": "https://example.invalid/a",
            "observed_at": datetime(2026, 3, 8, tzinfo=timezone.utc),
            "excerpt_lines": ["line"],
            "failed_steps": ["Run ruff"],
        }
    )
    with_summary = without_summary.model_copy(update={"summary": "failed"})

    assert failing_checks_module._selection_score(with_summary)[1] == (
        failing_checks_module._selection_score(without_summary)[1] + 1
    )
