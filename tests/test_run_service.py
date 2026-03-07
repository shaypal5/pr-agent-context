from __future__ import annotations

from conftest import load_json_fixture, load_text_fixture
from pr_agent_context.config import PullRequestRef, RunConfig
from pr_agent_context.services.run import run_service


class FakeGitHubClient:
    def __init__(self, *, review_threads_payload, workflow_jobs_payload, issue_comments_payload):
        self.review_threads_payload = review_threads_payload
        self.workflow_jobs_payload = workflow_jobs_payload
        self.issue_comments_payload = list(issue_comments_payload)
        self.created_bodies: list[str] = []
        self.updated_bodies: list[str] = []
        self.deleted_ids: list[int] = []

    def graphql(self, query, variables):
        return self.review_threads_payload["data"]

    def request_json(self, method, path, params=None, payload=None, extra_headers=None):
        if method == "GET" and "/actions/runs/" in path and path.endswith("/jobs"):
            return self.workflow_jobs_payload
        if method == "GET" and path.endswith("/comments"):
            return self.issue_comments_payload
        if method == "POST" and path.endswith("/comments"):
            created = {
                "id": 500,
                "body": payload["body"],
                "html_url": "https://github.com/shaypal5/example/pull/17#issuecomment-500",
                "created_at": "2026-03-07T09:30:00Z",
                "updated_at": "2026-03-07T09:30:00Z",
                "user": {
                    "login": "github-actions[bot]",
                    "type": "Bot"
                }
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

    def request_text(self, method, path, params=None, extra_headers=None):
        job_id = int(path.split("/")[-2])
        if job_id == 1001:
            return load_text_fixture("github/logs/pytest_failure.log")
        if job_id == 1002:
            return load_text_fixture("github/logs/pre_commit_failure.log")
        if job_id == 1003:
            return load_text_fixture("github/logs/timeout_failure.log")
        raise AssertionError(f"Unknown job log request: {path}")


def _build_config(tmp_path):
    return RunConfig(
        github_token="token",
        pull_request=PullRequestRef(owner="shaypal5", repo="example", number=17),
        run_id=1,
        run_attempt=1,
        workspace=tmp_path,
        prompt_preamble="Repository: foldermix",
        max_review_threads=50,
        max_failed_jobs=20,
        max_log_lines_per_job=6,
        delete_comment_when_empty=True,
        skip_comment_on_readonly_token=True,
        github_output_path=tmp_path / "github-output.txt",
    )


def _read_outputs(path):
    lines = path.read_text(encoding="utf-8").splitlines()
    return dict(line.split("=", maxsplit=1) for line in lines)


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
    assert outputs["failed_job_count"] == "3"
    assert outputs["comment_written"] == "true"


def test_run_service_deletes_managed_comments_when_no_actionable_items(
    tmp_path,
    issue_comments_payload,
):
    empty_review_threads = {
        "data": {
            "repository": {
                "pullRequest": {
                    "reviewThreads": {
                        "pageInfo": {"hasNextPage": False, "endCursor": None},
                        "nodes": []
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
    assert client.deleted_ids == [2, 3]
    assert outputs["has_actionable_items"] == "false"
    assert outputs["comment_written"] == "false"


def test_run_service_updates_existing_managed_comment_without_reordering(
    tmp_path,
    issue_comments_payload,
):
    client = FakeGitHubClient(
        review_threads_payload=load_json_fixture("github/review_threads.json"),
        workflow_jobs_payload=load_json_fixture("github/workflow_jobs.json"),
        issue_comments_payload=issue_comments_payload,
    )
    config = _build_config(tmp_path)

    assert run_service(config, client=client) == 0

    assert client.deleted_ids == [2]
    assert len(client.updated_bodies) == 1
    updated_body = client.updated_bodies[0]
    assert updated_body.index("## COPILOT-1") < updated_body.index("## REVIEW-1")
    assert updated_body.index("## FAIL-1") < updated_body.index("## FAIL-2")
