from __future__ import annotations

from conftest import load_text_fixture
from pr_agent_context.github.workflow_jobs import parse_failed_jobs, split_job_display_name


def test_split_job_display_name():
    assert split_job_display_name("smoke (ubuntu-latest, 3.12)") == (
        "smoke",
        "ubuntu-latest, 3.12",
    )
    assert split_job_display_name("lint") == ("lint", None)


def test_parse_failed_jobs_sorts_and_trims_logs(workflow_jobs_payload):
    logs = {
        1001: load_text_fixture("github/logs/pytest_failure.log"),
        1002: load_text_fixture("github/logs/pre_commit_failure.log"),
        1003: load_text_fixture("github/logs/timeout_failure.log"),
    }

    failures = parse_failed_jobs(
        workflow_jobs_payload["jobs"],
        log_fetcher=lambda job_id: logs[job_id],
        max_failed_jobs=10,
        max_log_lines_per_job=6,
    )

    assert [failure.job_id for failure in failures] == [1002, 1001, 1003]
    assert failures[0].failed_steps == ["Run pre-commit"]
    assert "would reformat src/example.py" in failures[0].excerpt_lines
    assert len(failures[1].excerpt_lines) <= 6
    assert failures[2].conclusion == "timed_out"
