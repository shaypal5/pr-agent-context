from __future__ import annotations

import json

import pytest

from pr_agent_context.config import (
    RunConfig,
    _extract_pull_request_number,
    _extract_pull_request_shas,
    _parse_bool,
)


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
    template_path = tmp_path / "prompt-template.md"
    template_path.write_text("{{ opening_instructions }}", encoding="utf-8")

    config = RunConfig.from_env(
        {
            "GITHUB_REPOSITORY": "shaypal5/example",
            "GITHUB_EVENT_PATH": str(event_path),
            "GITHUB_RUN_ID": "123",
            "GITHUB_RUN_ATTEMPT": "4",
            "GITHUB_TOKEN": "token",
            "GITHUB_OUTPUT": str(output_path),
            "PR_AGENT_CONTEXT_TOOL_REF": "v3",
            "PR_AGENT_CONTEXT_WORKSPACE": str(tmp_path),
            "PR_AGENT_CONTEXT_INCLUDE_REVIEW_COMMENTS": "false",
            "PR_AGENT_CONTEXT_INCLUDE_FAILING_JOBS": "true",
            "PR_AGENT_CONTEXT_PROMPT_PREAMBLE": "Repository: example",
            "PR_AGENT_CONTEXT_PROMPT_TEMPLATE_FILE": str(template_path),
            "PR_AGENT_CONTEXT_MAX_REVIEW_THREADS": "12",
            "PR_AGENT_CONTEXT_MAX_FAILED_JOBS": "7",
            "PR_AGENT_CONTEXT_MAX_LOG_LINES_PER_JOB": "33",
            "PR_AGENT_CONTEXT_TARGET_PATCH_COVERAGE": "92.5",
            "PR_AGENT_CONTEXT_INCLUDE_PATCH_COVERAGE": "true",
            "PR_AGENT_CONTEXT_COVERAGE_ARTIFACT_PREFIX": "custom-prefix",
            "PR_AGENT_CONTEXT_FORCE_PATCH_COVERAGE_SECTION": "true",
            "PR_AGENT_CONTEXT_COPILOT_AUTHOR_PATTERNS": "copilot-reviewer,re:copilot.*bot",
            "PR_AGENT_CONTEXT_DEBUG_ARTIFACTS": "true",
            "PR_AGENT_CONTEXT_DEBUG_ARTIFACT_PREFIX": "debug-prefix",
            "PR_AGENT_CONTEXT_DELETE_COMMENT_WHEN_EMPTY": "false",
            "PR_AGENT_CONTEXT_SKIP_COMMENT_ON_READONLY_TOKEN": "true",
            "PR_AGENT_CONTEXT_COVERAGE_ARTIFACTS_DIR": str(coverage_dir),
        }
    )

    assert config.tool_ref == "v3"
    assert config.pull_request.owner == "shaypal5"
    assert config.pull_request.repo == "example"
    assert config.pull_request.number == 17
    assert config.pull_request.base_sha == "abc123"
    assert config.pull_request.head_sha == "def456"
    assert config.run_id == 123
    assert config.run_attempt == 4
    assert config.workspace == tmp_path
    assert config.include_review_comments is False
    assert config.include_failing_jobs is True
    assert config.prompt_preamble == "Repository: example"
    assert config.prompt_template_file == template_path.resolve()
    assert config.max_review_threads == 12
    assert config.max_failed_jobs == 7
    assert config.max_log_lines_per_job == 33
    assert config.target_patch_coverage == 92.5
    assert config.include_patch_coverage is True
    assert config.coverage_artifact_prefix == "custom-prefix"
    assert config.force_patch_coverage_section is True
    assert config.copilot_author_patterns.exact_logins == ("copilot-reviewer",)
    assert config.copilot_author_patterns.regex_patterns == ("copilot.*bot",)
    assert config.debug_artifacts is True
    assert config.debug_artifact_prefix == "debug-prefix"
    assert config.delete_comment_when_empty is False
    assert config.skip_comment_on_readonly_token is True
    assert config.coverage_artifacts_dir == coverage_dir
    assert config.debug_artifacts_dir == tmp_path / "pr-agent-context-debug"
    assert config.github_output_path == output_path


def test_run_config_rejects_empty_regex_pattern(tmp_path):
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

    with pytest.raises(ValueError, match="Empty regex pattern"):
        RunConfig.from_env(
            {
                "GITHUB_REPOSITORY": "shaypal5/example",
                "GITHUB_EVENT_PATH": str(event_path),
                "GITHUB_RUN_ID": "123",
                "GITHUB_TOKEN": "token",
                "PR_AGENT_CONTEXT_WORKSPACE": str(tmp_path),
                "PR_AGENT_CONTEXT_COPILOT_AUTHOR_PATTERNS": "re:   ",
            }
        )


def test_run_config_rejects_missing_template_path(tmp_path):
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

    with pytest.raises(ValueError, match="Configured path does not exist"):
        RunConfig.from_env(
            {
                "GITHUB_REPOSITORY": "shaypal5/example",
                "GITHUB_EVENT_PATH": str(event_path),
                "GITHUB_RUN_ID": "123",
                "GITHUB_TOKEN": "token",
                "PR_AGENT_CONTEXT_WORKSPACE": str(tmp_path),
                "PR_AGENT_CONTEXT_PROMPT_TEMPLATE_FILE": ".github/missing-template.md",
            }
        )


def test_config_private_helpers_cover_bool_and_event_fallbacks():
    assert _parse_bool(True, default=False) is True
    assert _extract_pull_request_number({"number": 42}) == 42

    with pytest.raises(ValueError, match="Unable to determine pull request number"):
        _extract_pull_request_number({})

    with pytest.raises(ValueError, match="Unable to determine pull request SHAs"):
        _extract_pull_request_shas({})

    with pytest.raises(ValueError, match="Unable to determine pull request SHAs"):
        _extract_pull_request_shas({"pull_request": {"base": {}, "head": "not-a-mapping"}})

    with pytest.raises(ValueError, match="missing base/head SHAs"):
        _extract_pull_request_shas(
            {"pull_request": {"base": {"sha": ""}, "head": {"sha": "head123"}}}
        )
