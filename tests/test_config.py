from __future__ import annotations

import json

from pr_agent_context.config import RunConfig


def test_run_config_from_env(tmp_path):
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps(
            {
                "pull_request": {
                    "number": 17,
                    "base": {"sha": "abc123"},
                    "head": {"sha": "def456"},
                }
            }
        ),
        encoding="utf-8",
    )
    output_path = tmp_path / "github-output.txt"
    coverage_dir = tmp_path / "coverage-artifacts"

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
            "PR_AGENT_CONTEXT_TARGET_PATCH_COVERAGE": "92.5",
            "PR_AGENT_CONTEXT_INCLUDE_PATCH_COVERAGE": "true",
            "PR_AGENT_CONTEXT_COVERAGE_ARTIFACT_PREFIX": "custom-prefix",
            "PR_AGENT_CONTEXT_FORCE_PATCH_COVERAGE_SECTION": "true",
            "PR_AGENT_CONTEXT_DELETE_COMMENT_WHEN_EMPTY": "false",
            "PR_AGENT_CONTEXT_SKIP_COMMENT_ON_READONLY_TOKEN": "true",
            "PR_AGENT_CONTEXT_COVERAGE_ARTIFACTS_DIR": str(coverage_dir),
        }
    )

    assert config.pull_request.owner == "shaypal5"
    assert config.pull_request.repo == "example"
    assert config.pull_request.number == 17
    assert config.pull_request.base_sha == "abc123"
    assert config.pull_request.head_sha == "def456"
    assert config.run_id == 123
    assert config.run_attempt == 4
    assert config.workspace == tmp_path
    assert config.prompt_preamble == "Repository: example"
    assert config.max_review_threads == 12
    assert config.max_failed_jobs == 7
    assert config.max_log_lines_per_job == 33
    assert config.target_patch_coverage == 92.5
    assert config.include_patch_coverage is True
    assert config.coverage_artifact_prefix == "custom-prefix"
    assert config.force_patch_coverage_section is True
    assert config.delete_comment_when_empty is False
    assert config.skip_comment_on_readonly_token is True
    assert config.coverage_artifacts_dir == coverage_dir
    assert config.github_output_path == output_path
