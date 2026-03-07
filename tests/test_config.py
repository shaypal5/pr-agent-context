from __future__ import annotations

import json

from pr_agent_context.config import RunConfig


def test_run_config_from_env(tmp_path):
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps({"pull_request": {"number": 17}}), encoding="utf-8")
    output_path = tmp_path / "github-output.txt"

    config = RunConfig.from_env(
        {
            "GITHUB_REPOSITORY": "shaypal5/example",
            "GITHUB_EVENT_PATH": str(event_path),
            "GITHUB_RUN_ID": "123",
            "GITHUB_RUN_ATTEMPT": "4",
            "GITHUB_TOKEN": "token",
            "GITHUB_OUTPUT": str(output_path),
            "PR_AGENT_CONTEXT_WORKSPACE": str(tmp_path),
            "PR_AGENT_CONTEXT_PROMPT_PREAMBLE": "Repository: example",
            "PR_AGENT_CONTEXT_MAX_REVIEW_THREADS": "12",
            "PR_AGENT_CONTEXT_MAX_FAILED_JOBS": "7",
            "PR_AGENT_CONTEXT_MAX_LOG_LINES_PER_JOB": "33",
            "PR_AGENT_CONTEXT_DELETE_COMMENT_WHEN_EMPTY": "false",
            "PR_AGENT_CONTEXT_SKIP_COMMENT_ON_READONLY_TOKEN": "true"
        }
    )

    assert config.pull_request.owner == "shaypal5"
    assert config.pull_request.repo == "example"
    assert config.pull_request.number == 17
    assert config.run_id == 123
    assert config.run_attempt == 4
    assert config.workspace == tmp_path
    assert config.prompt_preamble == "Repository: example"
    assert config.max_review_threads == 12
    assert config.max_failed_jobs == 7
    assert config.max_log_lines_per_job == 33
    assert config.delete_comment_when_empty is False
    assert config.skip_comment_on_readonly_token is True
    assert config.github_output_path == output_path
