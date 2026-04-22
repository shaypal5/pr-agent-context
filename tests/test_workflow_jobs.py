from __future__ import annotations

from io import BytesIO
from zipfile import ZipFile

from conftest import load_text_fixture
from pr_agent_context.github.workflow_jobs import (
    _extract_grouped_step_name,
    _group_step_blocks,
    _normalize_step_name,
    _trim_trailing_blank_lines,
    collect_failed_jobs,
    extract_failed_step_output,
    extract_failed_step_output_lines,
    extract_log_text,
    parse_failed_jobs,
    split_job_display_name,
    trim_log_excerpt,
    trim_log_excerpt_lines,
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
    assert trim_log_excerpt("one\ntwo\nthree\nfour", failed_steps=["Run pytest"], max_lines=0) == []
    assert trim_log_excerpt("one\ntwo\nthree\nfour", failed_steps=["Run pytest"], max_lines=2) == [
        "three",
        "four",
    ]


def test_trim_log_excerpt_lines_handles_no_matches_and_capacity_limit():
    lines = [
        "intro",
        "Run pytest",
        "context before",
        "context after",
        "##[error]Process completed with exit code 1.",
        "tail",
    ]

    assert trim_log_excerpt_lines([], failed_steps=["Run pytest"], max_lines=4) == []
    assert trim_log_excerpt_lines(lines, failed_steps=["Run mypy"], max_lines=3) == [
        "context after",
        "##[error]Process completed with exit code 1.",
        "tail",
    ]
    assert trim_log_excerpt_lines(lines, failed_steps=["Run pytest"], max_lines=2) == [
        "intro",
        "Run pytest",
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


def test_extract_failed_step_output_trims_trailing_blank_lines():
    log_text = "\n".join(
        [
            "2026-03-07T10:00:00Z ##[group]Run mypy",
            "src/example.py:1: error: boom",
            "Found 1 error in 1 file",
            "",
            "   ",
        ]
    )

    step_name, lines = extract_failed_step_output(
        log_text,
        failed_steps=["Run mypy"],
        max_lines=10,
    )

    assert step_name == "mypy"
    assert lines[-1] == "Found 1 error in 1 file"


def test_extract_failed_step_output_handles_empty_and_duplicate_groups():
    assert extract_failed_step_output("", failed_steps=["Run mypy"], max_lines=10) == (None, [])
    assert extract_failed_step_output(
        "plain log\nwithout groups",
        failed_steps=["Run mypy"],
        max_lines=10,
    ) == (None, [])
    duplicate_log = "\n".join(
        [
            "2026-03-07T10:00:00Z ##[group]Run mypy",
            "first",
            "2026-03-07T10:00:01Z ##[group]Run mypy",
            "second",
        ]
    )
    assert extract_failed_step_output(
        duplicate_log,
        failed_steps=["Run mypy"],
        max_lines=10,
    ) == (None, [])
    assert extract_failed_step_output(
        duplicate_log,
        failed_steps=[],
        max_lines=10,
    ) == (None, [])
    assert extract_failed_step_output(
        duplicate_log,
        failed_steps=["Run mypy"],
        max_lines=0,
    ) == (None, [])


def test_extract_failed_step_output_lines_prefers_unique_failed_step_match_order():
    lines = [
        "2026-03-07T10:00:00Z ##[group]Run lint",
        "lint failed",
        "##[endgroup]",
        "2026-03-07T10:00:01Z ##[group]Run mypy",
        "mypy failed",
        "##[endgroup]",
    ]

    assert extract_failed_step_output_lines(
        lines,
        failed_steps=["Run pytest", "Run mypy"],
        max_lines=10,
    ) == ("mypy", ["2026-03-07T10:00:01Z ##[group]Run mypy", "mypy failed", "##[endgroup]"])


def test_extract_failed_step_output_lines_scans_only_relevant_failed_steps():
    lines = [
        "2026-03-07T10:00:00Z ##[group]Run setup",
        "setup output",
        "##[endgroup]",
        "2026-03-07T10:00:01Z ##[group]Run lint",
        "lint output",
        "##[endgroup]",
        "2026-03-07T10:00:02Z ##[group]Run pytest",
        "tests/test_example.py::test_behavior FAILED",
        "##[error]Process completed with exit code 1.",
        "##[endgroup]",
        "2026-03-07T10:00:03Z ##[group]Run summary",
        "summary output",
        "##[endgroup]",
    ]

    assert extract_failed_step_output_lines(
        lines,
        failed_steps=["Run pytest"],
        max_lines=10,
    ) == (
        "pytest",
        [
            "2026-03-07T10:00:02Z ##[group]Run pytest",
            "tests/test_example.py::test_behavior FAILED",
            "##[error]Process completed with exit code 1.",
            "##[endgroup]",
        ],
    )


def test_extract_failed_step_output_lines_skips_ambiguous_first_match_for_later_unique_step():
    lines = [
        "2026-03-07T10:00:00Z ##[group]Run pytest",
        "first pytest block",
        "##[endgroup]",
        "2026-03-07T10:00:01Z ##[group]Run mypy",
        "mypy failed",
        "##[endgroup]",
        "2026-03-07T10:00:02Z ##[group]Run pytest",
        "second pytest block",
        "##[endgroup]",
    ]

    assert extract_failed_step_output_lines(
        lines,
        failed_steps=["Run pytest", "Run mypy"],
        max_lines=10,
    ) == ("mypy", ["2026-03-07T10:00:01Z ##[group]Run mypy", "mypy failed", "##[endgroup]"])


def test_extract_failed_step_output_lines_handles_duplicate_and_unterminated_candidate_groups():
    duplicate_lines = [
        "2026-03-07T10:00:00Z ##[group]Run pytest",
        "first pytest block",
        "##[endgroup]",
        "2026-03-07T10:00:01Z ##[group]Run pytest",
        "second pytest block",
        "##[endgroup]",
    ]
    unterminated_lines = [
        "2026-03-07T10:00:00Z ##[group]Run pytest",
        "tests/test_example.py::test_behavior FAILED",
        "",
        "   ",
    ]

    assert extract_failed_step_output_lines(
        duplicate_lines,
        failed_steps=["Run pytest"],
        max_lines=10,
    ) == (None, [])
    assert extract_failed_step_output_lines(
        unterminated_lines,
        failed_steps=["Run pytest"],
        max_lines=10,
    ) == (
        "pytest",
        [
            "2026-03-07T10:00:00Z ##[group]Run pytest",
            "tests/test_example.py::test_behavior FAILED",
        ],
    )


def test_step_block_helpers_cover_grouping_and_normalization_edges():
    lines = [
        "before group",
        "2026-03-07T10:00:00Z ##[group]Run mypy",
        "line 1",
        "##[endgroup]",
        "2026-03-07T10:00:01Z ##[group]Run pytest",
        "line 2",
    ]

    assert _extract_grouped_step_name("plain line") is None
    assert _extract_grouped_step_name("2026-03-07T10:00:00Z ##[group]Run    ") is None
    assert _extract_grouped_step_name("2026-03-07T10:00:00Z ##[group]Run mypy") == "mypy"
    assert _normalize_step_name("  Run   mypy  ") == "mypy"
    assert _normalize_step_name("pytest  ") == "pytest"
    assert _trim_trailing_blank_lines(["line 1", "", "  "]) == ["line 1"]
    assert _group_step_blocks([]) == []
    assert _group_step_blocks(lines) == [
        ("mypy", ["2026-03-07T10:00:00Z ##[group]Run mypy", "line 1", "##[endgroup]"]),
        ("pytest", ["2026-03-07T10:00:01Z ##[group]Run pytest", "line 2"]),
    ]


def test_split_lines_helpers_match_string_helpers():
    log_text = load_text_fixture("github/logs/pytest_failure.log")
    log_lines = log_text.splitlines()

    assert trim_log_excerpt_lines(
        log_lines,
        failed_steps=["Run pytest"],
        max_lines=6,
    ) == trim_log_excerpt(log_text, failed_steps=["Run pytest"], max_lines=6)
    assert extract_failed_step_output_lines(
        log_lines,
        failed_steps=["Run pytest"],
        max_lines=20,
    ) == extract_failed_step_output(log_text, failed_steps=["Run pytest"], max_lines=20)


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
