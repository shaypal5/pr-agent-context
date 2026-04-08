from __future__ import annotations

from io import BytesIO
from zipfile import ZipFile

from conftest import load_text_fixture
from pr_agent_context.github.workflow_jobs import (
    collect_failed_jobs,
    extract_failed_step_output,
    extract_log_text,
    parse_failed_jobs,
    split_job_display_name,
    trim_log_excerpt,
)


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
        max_actions_jobs=10,
        max_log_lines_per_job=6,
    )

    assert [failure.job_id for failure in failures] == [1002, 1001, 1003]
    assert failures[0].failed_steps == ["Run pre-commit"]
    assert "would reformat src/example.py" in failures[0].excerpt_lines
    assert len(failures[1].excerpt_lines) <= 6
    assert failures[2].conclusion == "timed_out"


def test_extract_log_text_reads_zip_archives():
    payload = BytesIO()
    with ZipFile(payload, "w") as archive:
        archive.writestr("0_step.txt", "first line\nsecond line\n")
        archive.writestr("1_step.txt", "third line\n")

    assert extract_log_text(payload.getvalue()) == "first line\nsecond line\nthird line"


def test_extract_log_text_falls_back_to_raw_payload_when_not_a_zip():
    assert extract_log_text(b"\xffraw text") == "\ufffdraw text"


def test_extract_log_text_falls_back_when_zip_contains_only_empty_files():
    payload = BytesIO()
    with ZipFile(payload, "w") as archive:
        archive.writestr("job.log", "")

    assert isinstance(extract_log_text(payload.getvalue()), str)


def test_extract_log_text_falls_back_when_zip_has_no_file_entries():
    payload = BytesIO()
    with ZipFile(payload, "w"):
        pass

    assert isinstance(extract_log_text(payload.getvalue()), str)


def test_trim_log_excerpt_handles_empty_and_anchorless_logs():
    assert trim_log_excerpt("", failed_steps=["Run pytest"], max_lines=3) == []
    assert trim_log_excerpt("one\ntwo\nthree\nfour", failed_steps=["Run pytest"], max_lines=2) == [
        "three",
        "four",
    ]


def test_extract_failed_step_output_returns_failed_group_block():
    step_name, lines = extract_failed_step_output(
        load_text_fixture("github/logs/pytest_failure.log"),
        failed_steps=["Run pytest"],
        max_lines=50,
    )

    assert step_name == "pytest"
    assert lines[0].endswith("##[group]Run pytest")
    assert lines[-1] == "##[error]Process completed with exit code 1."


def test_extract_failed_step_output_falls_back_when_step_cannot_be_matched():
    step_name, lines = extract_failed_step_output(
        load_text_fixture("github/logs/pytest_failure.log"),
        failed_steps=["Run mypy"],
        max_lines=50,
    )

    assert step_name is None
    assert lines == []


def test_extract_failed_step_output_applies_line_cap():
    log_text = "\n".join(
        [
            "2026-03-07T10:00:00Z ##[group]Run mypy",
            "line 1",
            "line 2",
            "line 3",
            "line 4",
            "##[error]Process completed with exit code 1.",
        ]
    )

    step_name, lines = extract_failed_step_output(
        log_text,
        failed_steps=["Run mypy"],
        max_lines=3,
    )

    assert step_name == "mypy"
    assert lines == [
        "2026-03-07T10:00:00Z ##[group]Run mypy",
        "line 1",
        "line 2",
    ]


class _PagedWorkflowJobsClient:
    def __init__(self) -> None:
        self.pages: list[int] = []

    def request_json(self, method, path, params=None):  # noqa: ANN001
        assert method == "GET"
        assert path.endswith("/jobs")
        page = params["page"]
        self.pages.append(page)
        if page == 1:
            return {
                "jobs": [
                    {
                        "id": index,
                        "name": f"success-{index}",
                        "workflow_name": "CI",
                        "conclusion": "success",
                        "html_url": f"https://example.invalid/{index}",
                        "steps": [],
                    }
                    for index in range(1, 101)
                ]
            }
        return {
            "jobs": [
                {
                    "id": 999,
                    "name": "smoke (ubuntu-latest, 3.12)",
                    "workflow_name": "CI",
                    "conclusion": "failure",
                    "html_url": "https://example.invalid/999",
                    "steps": [{"name": "Run pytest", "conclusion": "failure"}],
                }
            ]
        }

    def request_bytes(self, method, path):  # noqa: ANN001
        assert method == "GET"
        assert path.endswith("/999/logs")
        payload = BytesIO()
        with ZipFile(payload, "w") as archive:
            archive.writestr("job.log", "line 1\nERROR: failed\nline 3\n")
        return payload.getvalue()


def test_collect_failed_jobs_handles_pagination():
    client = _PagedWorkflowJobsClient()

    failures = collect_failed_jobs(
        client,
        owner="shaypal5",
        repo="example",
        run_id=123,
        run_attempt=1,
        max_actions_jobs=5,
        max_log_lines_per_job=4,
    )

    assert client.pages == [1, 2]
    assert [failure.job_id for failure in failures] == [999]
