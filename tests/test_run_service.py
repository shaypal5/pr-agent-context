from __future__ import annotations

import io
import json
import subprocess
from contextlib import redirect_stdout
from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest
from coverage import Coverage

from conftest import load_json_fixture, load_text_fixture
from pr_agent_context.config import PullRequestRef, RunConfig
from pr_agent_context.services.run import _write_outputs, run_service


class FakeGitHubClient:
    def __init__(
        self,
        *,
        review_threads_payload,
        workflow_jobs_payload,
        issue_comments_payload,
        workflow_runs_payload=None,
        check_runs_payload=None,
        commit_status_payload=None,
    ):
        self.review_threads_payload = review_threads_payload
        self.workflow_jobs_payload = workflow_jobs_payload
        self.issue_comments_payload = list(issue_comments_payload)
        self.workflow_runs_payload = workflow_runs_payload or {"workflow_runs": []}
        self.check_runs_payload = check_runs_payload or {"check_runs": []}
        self.commit_status_payload = commit_status_payload or {"statuses": []}
        self.created_bodies: list[str] = []
        self.updated_bodies: list[str] = []
        self.deleted_ids: list[int] = []

    def graphql(self, query, variables):
        return self.review_threads_payload["data"]

    def request_json(self, method, path, params=None, payload=None, extra_headers=None):
        if method == "GET" and path.endswith("/actions/runs"):
            return self.workflow_runs_payload
        if method == "GET" and "/actions/runs/" in path and path.endswith("/jobs"):
            return self.workflow_jobs_payload
        if method == "GET" and path.endswith("/check-runs"):
            return self.check_runs_payload
        if method == "GET" and path.endswith("/status"):
            return self.commit_status_payload
        if method == "GET" and path.endswith("/comments"):
            return self.issue_comments_payload
        if method == "POST" and path.endswith("/comments"):
            created = {
                "id": 500,
                "body": payload["body"],
                "html_url": "https://github.com/shaypal5/example/pull/17#issuecomment-500",
                "created_at": "2026-03-07T09:30:00Z",
                "updated_at": "2026-03-07T09:30:00Z",
                "user": {"login": "github-actions[bot]", "type": "Bot"},
            }
            self.created_bodies.append(payload["body"])
            self.issue_comments_payload.append(created)
            return created
        if method == "PATCH" and "/issues/comments/" in path:
            comment_id = int(path.rsplit("/", maxsplit=1)[-1])
            self.updated_bodies.append(payload["body"])
            for comment in self.issue_comments_payload:
                if comment["id"] == comment_id:
                    comment["body"] = payload["body"]
                    return comment
        if method == "DELETE" and "/issues/comments/" in path:
            comment_id = int(path.rsplit("/", maxsplit=1)[-1])
            self.deleted_ids.append(comment_id)
            self.issue_comments_payload = [
                comment for comment in self.issue_comments_payload if comment["id"] != comment_id
            ]
            return {}
        raise AssertionError(f"Unexpected call: {method} {path}")

    def request_bytes(self, method, path, params=None, extra_headers=None):
        job_id = int(path.split("/")[-2])
        if job_id == 1001:
            return _zip_bytes(load_text_fixture("github/logs/pytest_failure.log"))
        if job_id == 1002:
            return _zip_bytes(load_text_fixture("github/logs/pre_commit_failure.log"))
        if job_id == 1003:
            return _zip_bytes(load_text_fixture("github/logs/timeout_failure.log"))
        raise AssertionError(f"Unknown job log request: {path}")


class CrossRunGitHubClient(FakeGitHubClient):
    def __init__(self, *, review_threads_payload, issue_comments_payload):
        super().__init__(
            review_threads_payload=review_threads_payload,
            workflow_jobs_payload={"jobs": []},
            issue_comments_payload=issue_comments_payload,
            workflow_runs_payload=load_json_fixture("github/workflow_runs.json"),
            check_runs_payload=load_json_fixture("github/check_runs.json"),
            commit_status_payload=load_json_fixture("github/commit_status.json"),
        )
        self.workflow_jobs_by_run = {
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
        if method == "GET" and "/actions/runs/" in path and path.endswith("/jobs"):
            parts = path.split("/")
            run_id = int(parts[-4])
            run_attempt = int(parts[-2])
            if (run_id, run_attempt) == (203, 1):
                from pr_agent_context.github.api import GitHubApiError

                raise GitHubApiError(404, "Not Found", "")
            return self.workflow_jobs_by_run[(run_id, run_attempt)]
        return super().request_json(
            method,
            path,
            params=params,
            payload=payload,
            extra_headers=extra_headers,
        )

    def request_bytes(self, method, path, params=None, extra_headers=None):
        job_id = int(path.split("/")[-2])
        if job_id == 1101:
            return _zip_bytes(load_text_fixture("github/logs/pytest_failure.log"))
        if job_id == 1202:
            return _zip_bytes(load_text_fixture("github/logs/pre_commit_failure.log"))
        return super().request_bytes(method, path, params=params, extra_headers=extra_headers)


def _zip_bytes(text: str) -> bytes:
    buffer = BytesIO()
    with ZipFile(buffer, "w") as archive:
        archive.writestr("job.log", text)
    return buffer.getvalue()


def _build_config(tmp_path, **overrides):
    config = RunConfig(
        github_token="token",
        tool_ref="v4",
        pull_request=PullRequestRef(
            owner="shaypal5",
            repo="example",
            number=17,
            base_sha="abc123",
            head_sha="def456",
        ),
        run_id=100,
        run_attempt=2,
        workspace=tmp_path,
        prompt_preamble="Repository: foldermix",
        max_review_threads=50,
        include_cross_run_failures=False,
        include_external_checks=False,
        max_actions_jobs=20,
        max_log_lines_per_job=6,
        characters_per_line=100,
        include_patch_coverage=False,
        debug_artifacts=True,
        debug_artifacts_dir=tmp_path / "debug",
        delete_comment_when_empty=True,
        skip_comment_on_readonly_token=True,
        github_output_path=tmp_path / "github-output.txt",
    )
    return config.model_copy(update=overrides)


def _read_outputs(path):
    lines = path.read_text(encoding="utf-8").splitlines()
    return dict(line.split("=", maxsplit=1) for line in lines)


def _structured_log_lines(output: str) -> list[dict[str, object]]:
    return [
        json.loads(line)
        for line in output.splitlines()
        if line.startswith("{") and '"tool": "pr-agent-context"' in line
    ]


def test_write_outputs_returns_early_when_no_output_path(tmp_path):
    calls: list[tuple[str, str]] = []

    def fail_if_called(self, data, encoding="utf-8"):  # noqa: ARG001
        calls.append((str(self), data))
        raise AssertionError("write_text should not be called")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("pathlib.Path.write_text", fail_if_called)
    try:
        _write_outputs(
            None,
            unresolved_thread_count=1,
            failing_check_count=2,
            has_actionable_items=True,
            patch_coverage_percent=95.5,
            comment_written=True,
            comment_id=123,
            comment_url="https://example.invalid/comment/123",
            prompt_sha256="abc",
        )
    finally:
        monkeypatch.undo()

    assert calls == []


def _run_git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _write_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _build_coverage_data(script_path: Path, data_file: Path, invocation: str) -> None:
    coverage = Coverage(config_file=False, data_file=str(data_file))
    coverage.start()
    globals_dict = {"__name__": "__main__"}
    exec(
        compile(script_path.read_text(encoding="utf-8"), str(script_path), "exec"),
        globals_dict,
    )
    exec(invocation, globals_dict)
    coverage.stop()
    coverage.save()


def test_run_service_creates_managed_comment(tmp_path, issue_comments_payload):
    client = FakeGitHubClient(
        review_threads_payload=load_json_fixture("github/review_threads.json"),
        workflow_jobs_payload=load_json_fixture("github/workflow_jobs.json"),
        issue_comments_payload=[issue_comments_payload[0]],
    )
    config = _build_config(tmp_path)

    assert run_service(config, client=client) == 0

    outputs = _read_outputs(config.github_output_path)
    assert client.created_bodies
    assert outputs["unresolved_thread_count"] == "2"
    assert outputs["failing_check_count"] == "3"
    assert outputs["comment_written"] == "true"
    assert len(outputs["prompt_sha256"]) == 64


def test_run_service_publishes_all_clear_comment_when_no_actionable_items(
    tmp_path,
    issue_comments_payload,
):
    empty_review_threads = {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": [],
                    }
                }
            }
        }
    }
    no_failures_payload = {"jobs": []}
    client = FakeGitHubClient(
        review_threads_payload=empty_review_threads,
        workflow_jobs_payload=no_failures_payload,
        issue_comments_payload=issue_comments_payload,
    )
    config = _build_config(tmp_path)

    assert run_service(config, client=client) == 0

    outputs = _read_outputs(config.github_output_path)
    assert client.deleted_ids == []
    assert client.created_bodies
    assert "No actionable items were found in the enabled checks" in client.created_bodies[-1]
    assert outputs["has_actionable_items"] == "false"
    assert outputs["comment_written"] == "true"


def test_run_service_does_not_print_empty_prompt(
    tmp_path,
    issue_comments_payload,
):
    empty_review_threads = {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": [],
                    }
                }
            }
        }
    }
    client = FakeGitHubClient(
        review_threads_payload=empty_review_threads,
        workflow_jobs_payload={"jobs": []},
        issue_comments_payload=issue_comments_payload,
    )
    config = _build_config(tmp_path)
    stdout = io.StringIO()

    with redirect_stdout(stdout):
        assert run_service(config, client=client) == 0

    output = stdout.getvalue()
    assert "No actionable items were found" not in output
    events = _structured_log_lines(output)
    assert any(event["event"] == "start" for event in events)
    assert any(event["event"] == "comment_sync" for event in events)


def test_run_service_logs_runtime_diagnostics(tmp_path, issue_comments_payload):
    client = FakeGitHubClient(
        review_threads_payload=load_json_fixture("github/review_threads.json"),
        workflow_jobs_payload=load_json_fixture("github/workflow_jobs.json"),
        issue_comments_payload=[issue_comments_payload[0]],
    )
    config = _build_config(tmp_path)
    stdout = io.StringIO()

    with redirect_stdout(stdout):
        assert run_service(config, client=client) == 0

    events = {event["event"]: event for event in _structured_log_lines(stdout.getvalue())}
    assert events["start"]["version"]
    assert events["start"]["pull_request_number"] == 17
    assert events["start"]["head_sha"] == "def456"
    assert events["review_threads"] == {
        "count": 2,
        "enabled": True,
        "event": "review_threads",
        "tool": "pr-agent-context",
    }
    assert events["failing_checks"] == {
        "count": 3,
        "enabled": True,
        "event": "failing_checks",
        "source_counts": {"actions_job": 3},
        "tool": "pr-agent-context",
        "warning_count": 0,
    }
    assert events["render"]["event"] == "render"
    assert events["comment_sync"]["action"] == "created"
    assert events["summary"]["unresolved_thread_count"] == 2
    assert events["summary"]["failing_check_count"] == 3


def test_run_service_aggregates_pr_wide_failing_checks(tmp_path, issue_comments_payload):
    empty_review_threads = {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": [],
                    }
                }
            }
        }
    }
    client = CrossRunGitHubClient(
        review_threads_payload=empty_review_threads,
        issue_comments_payload=[issue_comments_payload[0]],
    )
    config = RunConfig(
        github_token="token",
        tool_ref="v4",
        pull_request=PullRequestRef(
            owner="shaypal5",
            repo="example",
            number=17,
            base_sha="abc123",
            head_sha="def456",
        ),
        run_id=202,
        run_attempt=2,
        workspace=tmp_path,
        include_review_comments=False,
        include_failing_checks=True,
        include_cross_run_failures=True,
        include_external_checks=True,
        include_patch_coverage=False,
        debug_artifacts=True,
        debug_artifacts_dir=tmp_path / "debug",
        delete_comment_when_empty=True,
        skip_comment_on_readonly_token=True,
        github_output_path=tmp_path / "github-output.txt",
    )

    assert run_service(config, client=client) == 0

    outputs = _read_outputs(config.github_output_path)
    failing_debug = json.loads(
        (config.debug_artifacts_dir / "failing-check-universe.json").read_text(encoding="utf-8")
    )
    prompt_text = (config.debug_artifacts_dir / "prompt.md").read_text(encoding="utf-8")

    assert outputs["failing_check_count"] == "4"
    assert failing_debug["deduped_source_counts"] == {
        "actions_job": 1,
        "actions_workflow_run": 1,
        "commit_status": 1,
        "external_check_run": 1,
    }
    assert "# Failing Workflows" in prompt_text
    assert "Type: External check run" in prompt_text
    assert "Type: Commit status" in prompt_text
    assert "Type: GitHub Actions workflow run" in prompt_text


def test_run_service_updates_existing_same_run_comment_without_reordering(
    tmp_path,
    issue_comments_payload,
):
    client = FakeGitHubClient(
        review_threads_payload=load_json_fixture("github/review_threads.json"),
        workflow_jobs_payload=load_json_fixture("github/workflow_jobs.json"),
        issue_comments_payload=issue_comments_payload,
    )
    config = _build_config(tmp_path, publish_mode="update_matching")

    assert run_service(config, client=client) == 0

    assert client.deleted_ids == []
    assert len(client.updated_bodies) == 1
    updated_body = client.updated_bodies[0]
    assert updated_body.index("## COPILOT-1") < updated_body.index("## REVIEW-1")
    assert updated_body.index("## FAIL-1") < updated_body.index("## FAIL-2")


def test_run_service_creates_new_comment_for_new_run_attempt(tmp_path, issue_comments_payload):
    client = FakeGitHubClient(
        review_threads_payload=load_json_fixture("github/review_threads.json"),
        workflow_jobs_payload=load_json_fixture("github/workflow_jobs.json"),
        issue_comments_payload=issue_comments_payload,
    )
    config = _build_config(tmp_path).model_copy(update={"run_attempt": 3})

    assert run_service(config, client=client) == 0

    assert client.updated_bodies == []
    assert len(client.created_bodies) == 1
    created_body = client.created_bodies[0]
    assert "run_attempt=3" in created_body.splitlines()[0]


def test_run_service_renders_actionable_patch_coverage_from_artifacts(
    tmp_path,
    issue_comments_payload,
):
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_git(repo, "init")
    _run_git(repo, "config", "user.name", "Test User")
    _run_git(repo, "config", "user.email", "test@example.com")
    module_path = repo / "src" / "pkg" / "module.py"
    _write_file(module_path, "def compute(flag):\n    return 1 if flag else 2\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "base")
    base_sha = _run_git(repo, "rev-parse", "HEAD")

    _write_file(
        module_path,
        "def compute(flag):\n"
        "    if flag:\n"
        "        value = 1\n"
        "    else:\n"
        "        value = 2\n"
        "    return value\n",
    )
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "head")
    head_sha = _run_git(repo, "rev-parse", "HEAD")

    coverage_dir = tmp_path / "coverage-artifacts" / "linux"
    coverage_dir.mkdir(parents=True)
    _build_coverage_data(module_path, coverage_dir / ".coverage.py311", "compute(True)")

    empty_review_threads = {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": [],
                    }
                }
            }
        }
    }
    client = FakeGitHubClient(
        review_threads_payload=empty_review_threads,
        workflow_jobs_payload={"jobs": []},
        issue_comments_payload=[issue_comments_payload[0]],
    )
    config = RunConfig(
        github_token="token",
        pull_request=PullRequestRef(
            owner="shaypal5",
            repo="example",
            number=17,
            base_sha=base_sha,
            head_sha=head_sha,
        ),
        run_id=1,
        run_attempt=1,
        workspace=repo,
        target_patch_coverage=100,
        include_patch_coverage=True,
        coverage_artifacts_dir=tmp_path / "coverage-artifacts",
        delete_comment_when_empty=True,
        skip_comment_on_readonly_token=True,
        github_output_path=tmp_path / "github-output.txt",
    )

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        assert run_service(config, client=client) == 0

    outputs = _read_outputs(config.github_output_path)
    assert outputs["has_actionable_items"] == "true"
    assert outputs["patch_coverage_percent"] == "75.0"
    assert len(outputs["prompt_sha256"]) == 64
    assert client.created_bodies
    assert "# Codecov/patch" in client.created_bodies[0]
    assert "- src/pkg/module.py: 5" in client.created_bodies[0]
    assert "# Codecov/patch" in stdout.getvalue()


def test_run_service_can_force_na_patch_coverage_section(
    tmp_path,
    issue_comments_payload,
):
    repo = tmp_path / "repo"
    repo.mkdir()
    _run_git(repo, "init")
    _run_git(repo, "config", "user.name", "Test User")
    _run_git(repo, "config", "user.email", "test@example.com")
    notes_path = repo / "src" / "pkg" / "notes.py"
    _write_file(notes_path, "# base\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "base")
    base_sha = _run_git(repo, "rev-parse", "HEAD")

    _write_file(notes_path, "# base\n# added comment\n")
    _run_git(repo, "add", ".")
    _run_git(repo, "commit", "-m", "head")
    head_sha = _run_git(repo, "rev-parse", "HEAD")

    empty_review_threads = {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": [],
                    }
                }
            }
        }
    }
    client = FakeGitHubClient(
        review_threads_payload=empty_review_threads,
        workflow_jobs_payload={"jobs": []},
        issue_comments_payload=[issue_comments_payload[0]],
    )
    config = RunConfig(
        github_token="token",
        pull_request=PullRequestRef(
            owner="shaypal5",
            repo="example",
            number=17,
            base_sha=base_sha,
            head_sha=head_sha,
        ),
        run_id=1,
        run_attempt=1,
        workspace=repo,
        include_patch_coverage=True,
        force_patch_coverage_section=True,
        coverage_artifacts_dir=tmp_path / "coverage-artifacts",
        delete_comment_when_empty=True,
        skip_comment_on_readonly_token=True,
        github_output_path=tmp_path / "github-output.txt",
    )

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        assert run_service(config, client=client) == 0

    outputs = _read_outputs(config.github_output_path)
    assert outputs["has_actionable_items"] == "false"
    assert outputs["patch_coverage_percent"] == ""
    assert client.created_bodies
    assert "no changed executable Python lines" in client.created_bodies[0]
    events = {event["event"]: event for event in _structured_log_lines(stdout.getvalue())}
    assert events["patch_result"]["event"] == "patch_result"
    assert events["comment_sync"]["action"] == "created"
    assert "No actionable items were found" not in stdout.getvalue()


def test_run_service_writes_debug_artifacts(tmp_path, issue_comments_payload):
    client = FakeGitHubClient(
        review_threads_payload=load_json_fixture("github/review_threads.json"),
        workflow_jobs_payload={"jobs": []},
        issue_comments_payload=[issue_comments_payload[0]],
    )
    config = _build_config(tmp_path)

    assert run_service(config, client=client) == 0

    summary = json.loads((config.debug_artifacts_dir / "summary.json").read_text(encoding="utf-8"))
    collected = json.loads(
        (config.debug_artifacts_dir / "collected-context.json").read_text(encoding="utf-8")
    )
    comment_sync = json.loads(
        (config.debug_artifacts_dir / "comment-sync.json").read_text(encoding="utf-8")
    )
    prompt_text = (config.debug_artifacts_dir / "prompt.md").read_text(encoding="utf-8")
    comment_body = (config.debug_artifacts_dir / "comment-body.md").read_text(encoding="utf-8")

    assert summary["tool_ref"] == "v4"
    assert summary["unresolved_thread_count"] == 2
    assert summary["failing_check_source_counts"] == {}
    assert "template_diagnostics" in summary
    assert collected["pull_request"]["number"] == 17
    assert (config.debug_artifacts_dir / "failing-check-universe.json").exists()
    assert comment_sync["sync_debug"]["current_identity"]["run_id"] == 100
    assert comment_sync["sync_debug"]["matched_existing_comment"] is False
    assert comment_sync["sync_debug"]["current_identity"]["generated_at"].endswith("+00:00")
    assert prompt_text.startswith("Repository: foldermix")
    assert comment_body.startswith(
        "<!-- pr-agent-context:managed-comment; schema=v5; publish_mode=append;"
    )
    assert "pr-agent-context report:\n```markdown\nRepository: foldermix" in comment_body
    assert "\nRun metadata:\n```\nTool ref: v4\n" in comment_body
    assert (config.debug_artifacts_dir / "coverage-source.json").exists() is False
    assert (config.debug_artifacts_dir / "pull-request-context.json").exists()
    assert (config.debug_artifacts_dir / "comment-sync.json").exists()


def test_run_service_refresh_mode_marks_review_wait_disabled(tmp_path, issue_comments_payload):
    client = FakeGitHubClient(
        review_threads_payload=load_json_fixture("github/review_threads.json"),
        workflow_jobs_payload={"jobs": []},
        issue_comments_payload=[issue_comments_payload[0]],
    )
    config = _build_config(tmp_path).model_copy(
        update={
            "execution_mode": "refresh",
            "wait_for_reviews_to_settle": False,
            "include_failing_checks": False,
            "include_patch_coverage": False,
        }
    )

    assert run_service(config, client=client) == 0

    collected = json.loads(
        (config.debug_artifacts_dir / "collected-context.json").read_text(encoding="utf-8")
    )
    assert collected["review_settlement_debug"]["skipped_reason"] == "refresh_wait_disabled"


def test_run_service_writes_coverage_source_debug_when_patch_coverage_enabled(
    tmp_path, issue_comments_payload, monkeypatch
):
    empty_review_threads = {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": [],
                    }
                }
            }
        }
    }
    client = FakeGitHubClient(
        review_threads_payload=empty_review_threads,
        workflow_jobs_payload={"jobs": []},
        issue_comments_payload=[issue_comments_payload[0]],
    )
    config = _build_config(tmp_path).model_copy(
        update={
            "include_review_comments": False,
            "include_failing_checks": False,
            "include_patch_coverage": True,
            "coverage_artifacts_dir": tmp_path / "missing-artifacts",
        }
    )
    monkeypatch.setattr("pr_agent_context.services.run.collect_changed_lines", lambda *_, **__: {})

    assert run_service(config, client=client) == 0

    coverage_source = json.loads(
        (config.debug_artifacts_dir / "coverage-source.json").read_text(encoding="utf-8")
    )
    assert "resolution" in coverage_source


def test_run_service_uses_review_settlement_and_skips_debug_artifacts_when_disabled(
    tmp_path,
    issue_comments_payload,
    monkeypatch,
):
    client = FakeGitHubClient(
        review_threads_payload=load_json_fixture("github/review_threads.json"),
        workflow_jobs_payload={"jobs": []},
        issue_comments_payload=[issue_comments_payload[0]],
    )
    config = _build_config(
        tmp_path,
        execution_mode="refresh",
        wait_for_reviews_to_settle=True,
        debug_artifacts=False,
        include_failing_checks=False,
    )
    review_settle_called = {"value": False}

    def fake_wait_for_review_threads_to_settle(*args, **kwargs):  # noqa: ARG001
        review_settle_called["value"] = True
        return [], {
            "enabled": True,
            "settled": True,
            "timed_out": False,
            "skipped_reason": "",
            "poll_count": 1,
            "elapsed_seconds": 0.0,
            "thread_count": 0,
        }

    monkeypatch.setattr(
        "pr_agent_context.services.run.wait_for_review_threads_to_settle",
        fake_wait_for_review_threads_to_settle,
    )
    monkeypatch.setattr(
        "pr_agent_context.services.run._write_debug_artifacts",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("should not write debug artifacts")
        ),
    )

    assert run_service(config, client=client) == 0
    assert review_settle_called["value"] is True


def test_run_service_refresh_mode_suppresses_noop_comment_by_default(tmp_path):
    empty_review_threads = {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": [],
                    }
                }
            }
        }
    }
    client = FakeGitHubClient(
        review_threads_payload=empty_review_threads,
        workflow_jobs_payload={"jobs": []},
        issue_comments_payload=[],
    )
    config = _build_config(
        tmp_path,
        execution_mode="refresh",
        publish_mode="update_latest_scoped",
        include_failing_checks=False,
        include_patch_coverage=False,
    )

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        assert run_service(config, client=client) == 0

    outputs = _read_outputs(config.github_output_path)
    events = {event["event"]: event for event in _structured_log_lines(stdout.getvalue())}

    assert outputs["comment_written"] == "false"
    assert client.created_bodies == []
    assert events["render"]["should_publish_comment"] is False
    assert events["comment_sync"]["action"] == "noop_no_comment"


def test_run_service_refresh_mode_deletes_only_refresh_scoped_comment_when_empty(tmp_path):
    empty_review_threads = {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": [],
                    }
                }
            }
        }
    }
    issue_comments_payload = [
        {
            "id": 30,
            "body": (
                "<!-- pr-agent-context:managed-comment; schema=v5; "
                "publish_mode=append; execution_mode=ci; pr=17; "
                "head_sha=def456; trigger_event=pull_request; "
                "generated_at=2026-03-07T08:50:00+00:00; tool_ref=v4; run_id=100; "
                "run_attempt=1 -->\n```markdown\nci body\n```"
            ),
            "html_url": "https://github.com/shaypal5/example/pull/17#issuecomment-30",
            "created_at": "2026-03-07T08:50:00Z",
            "updated_at": "2026-03-07T08:50:00Z",
            "user": {"login": "github-actions[bot]", "type": "Bot"},
        },
        {
            "id": 31,
            "body": (
                "<!-- pr-agent-context:managed-comment; schema=v5; "
                "publish_mode=append; execution_mode=refresh; pr=17; "
                "head_sha=def456; trigger_event=check_run; "
                "generated_at=2026-03-07T08:51:00+00:00; tool_ref=v4; run_id=101; "
                "run_attempt=1 -->\n```markdown\nrefresh body\n```"
            ),
            "html_url": "https://github.com/shaypal5/example/pull/17#issuecomment-31",
            "created_at": "2026-03-07T08:51:00Z",
            "updated_at": "2026-03-07T08:51:00Z",
            "user": {"login": "github-actions[bot]", "type": "Bot"},
        },
    ]
    client = FakeGitHubClient(
        review_threads_payload=empty_review_threads,
        workflow_jobs_payload={"jobs": []},
        issue_comments_payload=issue_comments_payload,
    )
    config = _build_config(
        tmp_path,
        execution_mode="refresh",
        publish_mode="update_latest_scoped",
        include_failing_checks=False,
        include_patch_coverage=False,
    )

    stdout = io.StringIO()
    with redirect_stdout(stdout):
        assert run_service(config, client=client) == 0

    outputs = _read_outputs(config.github_output_path)
    events = {event["event"]: event for event in _structured_log_lines(stdout.getvalue())}

    assert client.deleted_ids == [31]
    assert outputs["comment_written"] == "false"
    assert events["comment_sync"]["action"] == "deleted"
