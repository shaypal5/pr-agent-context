from __future__ import annotations

import json

import pytest

from pr_agent_context.config import (
    PullRequestRef,
    RunConfig,
    TriggerContext,
    _extract_codecov_patch_target,
    _extract_is_fork,
    _extract_pull_request_number,
    _extract_pull_request_number_if_present,
    _extract_pull_request_shas,
    _extract_shas_from_pull_request_mapping,
    _extract_trigger_context,
    _parse_bool,
    _parse_coverage_selection_strategy,
    _parse_fork_behavior,
    _parse_percent_like_value,
    _parse_publish_mode,
    _resolve_execution_mode,
    load_pull_request_context_from_env,
    load_trigger_context_from_env,
    parse_bool_env,
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
            "PR_AGENT_CONTEXT_TOOL_REF": "v4",
            "PR_AGENT_CONTEXT_WORKSPACE": str(tmp_path),
            "PR_AGENT_CONTEXT_INCLUDE_REVIEW_COMMENTS": "false",
            "PR_AGENT_CONTEXT_INCLUDE_FAILING_CHECKS": "true",
            "PR_AGENT_CONTEXT_INCLUDE_CROSS_RUN_FAILURES": "false",
            "PR_AGENT_CONTEXT_INCLUDE_EXTERNAL_CHECKS": "false",
            "PR_AGENT_CONTEXT_WAIT_FOR_CHECKS_TO_SETTLE": "false",
            "PR_AGENT_CONTEXT_PROMPT_PREAMBLE": "Repository: example",
            "PR_AGENT_CONTEXT_PROMPT_TEMPLATE_FILE": str(template_path),
            "PR_AGENT_CONTEXT_MAX_REVIEW_THREADS": "12",
            "PR_AGENT_CONTEXT_MAX_ACTIONS_RUNS": "9",
            "PR_AGENT_CONTEXT_MAX_ACTIONS_JOBS": "7",
            "PR_AGENT_CONTEXT_MAX_EXTERNAL_CHECKS": "5",
            "PR_AGENT_CONTEXT_MAX_FAILING_CHECKS": "11",
            "PR_AGENT_CONTEXT_MAX_LOG_LINES_PER_JOB": "33",
            "PR_AGENT_CONTEXT_CHECK_SETTLE_TIMEOUT_SECONDS": "22",
            "PR_AGENT_CONTEXT_CHECK_SETTLE_POLL_INTERVAL_SECONDS": "3",
            "PR_AGENT_CONTEXT_CHARACTERS_PER_LINE": "88",
            "PR_AGENT_CONTEXT_TARGET_PATCH_COVERAGE": "92.5",
            "PR_AGENT_CONTEXT_INCLUDE_PATCH_COVERAGE": "true",
            "PR_AGENT_CONTEXT_PATCH_COVERAGE_SOURCE_MODE": "coverage_xml_artifact",
            "PR_AGENT_CONTEXT_COVERAGE_ARTIFACT_PREFIX": "custom-prefix",
            "PR_AGENT_CONTEXT_COVERAGE_REPORT_ARTIFACT_NAME": "coverage-xml",
            "PR_AGENT_CONTEXT_COVERAGE_REPORT_FILENAME": "reports/coverage.xml",
            "PR_AGENT_CONTEXT_FORCE_PATCH_COVERAGE_SECTION": "true",
            "PR_AGENT_CONTEXT_COPILOT_AUTHOR_PATTERNS": "copilot-reviewer,re:copilot.*bot",
            "PR_AGENT_CONTEXT_DEBUG_ARTIFACTS": "true",
            "PR_AGENT_CONTEXT_DEBUG_ARTIFACT_PREFIX": "debug-prefix",
            "PR_AGENT_CONTEXT_DELETE_COMMENT_WHEN_EMPTY": "false",
            "PR_AGENT_CONTEXT_SKIP_COMMENT_ON_READONLY_TOKEN": "true",
            "PR_AGENT_CONTEXT_PUBLISH_ALL_CLEAR_COMMENTS_IN_REFRESH": "true",
            "PR_AGENT_CONTEXT_PUBLISH_MODE": "update_latest_scoped",
            "PR_AGENT_CONTEXT_COVERAGE_ARTIFACTS_DIR": str(coverage_dir),
        }
    )

    assert config.tool_ref == "v4"
    assert config.pull_request.owner == "shaypal5"
    assert config.pull_request.repo == "example"
    assert config.pull_request.number == 17
    assert config.pull_request.base_sha == "abc123"
    assert config.pull_request.head_sha == "def456"
    assert config.run_id == 123
    assert config.run_attempt == 4
    assert config.workspace == tmp_path
    assert config.include_review_comments is False
    assert config.include_failing_checks is True
    assert config.include_cross_run_failures is False
    assert config.include_external_checks is False
    assert config.wait_for_checks_to_settle is False
    assert config.prompt_preamble == "Repository: example"
    assert config.prompt_template_file == template_path.resolve()
    assert config.max_review_threads == 12
    assert config.max_actions_runs == 9
    assert config.max_actions_jobs == 7
    assert config.max_external_checks == 5
    assert config.max_failing_checks == 11
    assert config.max_log_lines_per_job == 33
    assert config.check_settle_timeout_seconds == 22
    assert config.check_settle_poll_interval_seconds == 3
    assert config.characters_per_line == 88
    assert config.target_patch_coverage == 92.5
    assert config.include_patch_coverage is True
    assert config.patch_coverage_source_mode == "coverage_xml_artifact"
    assert config.coverage_artifact_prefix == "custom-prefix"
    assert config.coverage_report_artifact_name == "coverage-xml"
    assert config.coverage_report_filename == "reports/coverage.xml"
    assert config.force_patch_coverage_section is True
    assert config.copilot_author_patterns.exact_logins == ("copilot-reviewer",)
    assert config.copilot_author_patterns.regex_patterns == ("copilot.*bot",)
    assert config.debug_artifacts is True
    assert config.debug_artifact_prefix == "debug-prefix"
    assert config.delete_comment_when_empty is False
    assert config.skip_comment_on_readonly_token is True
    assert config.publish_all_clear_comments_in_refresh is True
    assert config.publish_mode == "update_latest_scoped"
    assert config.coverage_artifacts_dir == coverage_dir
    assert config.debug_artifacts_dir == tmp_path / "pr-agent-context-debug"
    assert config.github_output_path == output_path


def test_run_config_defaults_publish_all_clear_comments_in_refresh_to_false(tmp_path):
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

    config = RunConfig.from_env(
        {
            "GITHUB_REPOSITORY": "shaypal5/example",
            "GITHUB_EVENT_PATH": str(event_path),
            "GITHUB_RUN_ID": "123",
            "GITHUB_TOKEN": "token",
            "PR_AGENT_CONTEXT_WORKSPACE": str(tmp_path),
        }
    )

    assert config.publish_all_clear_comments_in_refresh is False
    assert config.patch_coverage_source_mode == "raw_coverage_artifacts"
    assert config.coverage_report_filename == "coverage.xml"
    assert config.target_patch_coverage == 100.0


def test_run_config_uses_codecov_patch_target_when_env_override_is_absent(tmp_path):
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
    (tmp_path / ".codecov.yml").write_text(
        "coverage:\n  status:\n    patch:\n      default:\n        target: 98%\n",
        encoding="utf-8",
    )

    config = RunConfig.from_env(
        {
            "GITHUB_REPOSITORY": "shaypal5/example",
            "GITHUB_EVENT_PATH": str(event_path),
            "GITHUB_RUN_ID": "123",
            "GITHUB_TOKEN": "token",
            "PR_AGENT_CONTEXT_WORKSPACE": str(tmp_path),
        }
    )

    assert config.target_patch_coverage == 98.0


def test_run_config_env_override_beats_codecov_patch_target(tmp_path):
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
    (tmp_path / "codecov.yml").write_text(
        "coverage:\n  status:\n    patch:\n      default:\n        target: 97%\n",
        encoding="utf-8",
    )

    config = RunConfig.from_env(
        {
            "GITHUB_REPOSITORY": "shaypal5/example",
            "GITHUB_EVENT_PATH": str(event_path),
            "GITHUB_RUN_ID": "123",
            "GITHUB_TOKEN": "token",
            "PR_AGENT_CONTEXT_WORKSPACE": str(tmp_path),
            "PR_AGENT_CONTEXT_TARGET_PATCH_COVERAGE": "92.5",
        }
    )

    assert config.target_patch_coverage == 92.5


def test_run_config_ignores_invalid_codecov_patch_target(tmp_path):
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
    (tmp_path / ".codecov.yml").write_text(
        "coverage:\n  status:\n    patch:\n      default:\n        target: 150%\n",
        encoding="utf-8",
    )

    config = RunConfig.from_env(
        {
            "GITHUB_REPOSITORY": "shaypal5/example",
            "GITHUB_EVENT_PATH": str(event_path),
            "GITHUB_RUN_ID": "123",
            "GITHUB_TOKEN": "token",
            "PR_AGENT_CONTEXT_WORKSPACE": str(tmp_path),
        }
    )

    assert config.target_patch_coverage == 100.0


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


def test_run_config_rejects_template_path_outside_workspace(tmp_path):
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
    outside_template = tmp_path.parent / "outside-template.md"
    outside_template.write_text("{{ opening_instructions }}", encoding="utf-8")

    with pytest.raises(ValueError, match="must be within the workspace"):
        RunConfig.from_env(
            {
                "GITHUB_REPOSITORY": "shaypal5/example",
                "GITHUB_EVENT_PATH": str(event_path),
                "GITHUB_RUN_ID": "123",
                "GITHUB_TOKEN": "token",
                "PR_AGENT_CONTEXT_WORKSPACE": str(tmp_path),
                "PR_AGENT_CONTEXT_PROMPT_TEMPLATE_FILE": str(outside_template),
            }
        )


def test_run_config_rejects_missing_report_artifact_name_for_xml_mode(tmp_path):
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

    with pytest.raises(ValueError, match="coverage_report_artifact_name is required"):
        RunConfig.from_env(
            {
                "GITHUB_REPOSITORY": "shaypal5/example",
                "GITHUB_EVENT_PATH": str(event_path),
                "GITHUB_RUN_ID": "123",
                "GITHUB_TOKEN": "token",
                "PR_AGENT_CONTEXT_WORKSPACE": str(tmp_path),
                "PR_AGENT_CONTEXT_PATCH_COVERAGE_SOURCE_MODE": "coverage_xml_artifact",
            }
        )


def test_run_config_rejects_unsupported_patch_coverage_source_mode(tmp_path):
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

    with pytest.raises(ValueError, match="Unsupported patch coverage source mode: xml"):
        RunConfig.from_env(
            {
                "GITHUB_REPOSITORY": "shaypal5/example",
                "GITHUB_EVENT_PATH": str(event_path),
                "GITHUB_RUN_ID": "123",
                "GITHUB_TOKEN": "token",
                "PR_AGENT_CONTEXT_WORKSPACE": str(tmp_path),
                "PR_AGENT_CONTEXT_PATCH_COVERAGE_SOURCE_MODE": "xml",
            }
        )


def test_run_config_treats_blank_debug_artifacts_dir_as_unset(tmp_path):
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

    config = RunConfig.from_env(
        {
            "GITHUB_REPOSITORY": "shaypal5/example",
            "GITHUB_EVENT_PATH": str(event_path),
            "GITHUB_RUN_ID": "123",
            "GITHUB_TOKEN": "token",
            "PR_AGENT_CONTEXT_WORKSPACE": str(tmp_path),
            "PR_AGENT_CONTEXT_DEBUG_ARTIFACTS": "true",
            "PR_AGENT_CONTEXT_DEBUG_ARTIFACTS_DIR": "   ",
        }
    )

    assert config.debug_artifacts_dir == tmp_path / "pr-agent-context-debug"


def test_config_private_helpers_cover_bool_and_event_fallbacks():
    assert _parse_bool(True, default=False) is True
    assert parse_bool_env("yes", default=False) is True
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


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("98%", 98.0),
        ("92.5", 92.5),
        (0.98, 98.0),
        (98, 98.0),
        (150, None),
        (-5, None),
        ("101%", None),
        ("-1", None),
        ("auto", None),
        ("", None),
        ("not-a-number", None),
    ],
)
def test_parse_percent_like_value(value, expected):
    assert _parse_percent_like_value(value) == expected


def test_extract_codecov_patch_target_prefers_default_then_named_entries():
    assert (
        _extract_codecov_patch_target(
            {
                "coverage": {
                    "status": {
                        "patch": {
                            "default": {"target": "98%"},
                            "strict": {"target": "95%"},
                        }
                    }
                }
            }
        )
        == 98.0
    )
    assert (
        _extract_codecov_patch_target(
            {
                "coverage": {
                    "status": {
                        "patch": {
                            "strict": {"target": "95%"},
                        }
                    }
                }
            }
        )
        == 95.0
    )
    assert (
        _extract_codecov_patch_target({"coverage": {"status": {"patch": {"target": "90%"}}}})
        == 90.0
    )
    assert (
        _extract_codecov_patch_target({"coverage": {"status": {"patch": {"target": "auto"}}}})
        is None
    )


def test_load_pull_request_context_from_env(tmp_path):
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps(
            {
                "pull_request": {
                    "number": 42,
                    "base": {"sha": "abc123"},
                    "head": {"sha": "def456"},
                }
            }
        ),
        encoding="utf-8",
    )

    owner, repo, pull_request = load_pull_request_context_from_env(
        {
            "GITHUB_REPOSITORY": "shaypal5/example",
            "GITHUB_EVENT_PATH": str(event_path),
        }
    )

    assert owner == "shaypal5"
    assert repo == "example"
    assert pull_request.number == 42
    assert pull_request.base_sha == "abc123"
    assert pull_request.head_sha == "def456"


def test_load_pull_request_context_from_env_rejects_missing_pull_request_context(
    tmp_path,
):
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps({"workflow_run": {"head_sha": "deadbeef", "pull_requests": []}}),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="Unable to determine pull request context"):
        load_pull_request_context_from_env(
            {
                "GITHUB_REPOSITORY": "shaypal5/example",
                "GITHUB_EVENT_PATH": str(event_path),
                "GITHUB_EVENT_NAME": "workflow_run",
            }
        )


@pytest.mark.parametrize(
    (
        "event_name",
        "event_payload",
        "expected_source",
        "expected_label",
        "expected_number",
        "expected_head_sha",
    ),
    [
        (
            "pull_request_review",
            {
                "action": "submitted",
                "pull_request": {
                    "number": 17,
                    "base": {"sha": "abc123"},
                    "head": {"sha": "def456", "repo": {"fork": True}},
                },
            },
            "pull_request_review:submitted",
            "review posted",
            17,
            "def456",
        ),
        (
            "pull_request_review_comment",
            {
                "action": "created",
                "pull_request": {
                    "number": 18,
                    "base": {"sha": "base123"},
                    "head": {"sha": "head123", "repo": {"fork": False}},
                },
            },
            "pull_request_review_comment:created",
            "review comment posted",
            18,
            "head123",
        ),
        (
            "workflow_run",
            {
                "action": "completed",
                "workflow_run": {
                    "head_sha": "deadbeef",
                    "pull_requests": [{"number": 21}],
                },
            },
            "workflow_run:completed",
            "workflow completed",
            21,
            "deadbeef",
        ),
        (
            "status",
            {
                "sha": "feedface",
            },
            "status",
            "status updated",
            None,
            "feedface",
        ),
    ],
)
def test_load_trigger_context_from_env_supports_refresh_events(
    tmp_path,
    event_name,
    event_payload,
    expected_source,
    expected_label,
    expected_number,
    expected_head_sha,
):
    event_path = tmp_path / f"{event_name}.json"
    event_path.write_text(json.dumps(event_payload), encoding="utf-8")

    trigger = load_trigger_context_from_env(
        {
            "GITHUB_EVENT_PATH": str(event_path),
            "GITHUB_EVENT_NAME": event_name,
            "PR_AGENT_CONTEXT_TRIGGER_EVENT_ACTION": str(event_payload.get("action") or ""),
        }
    )

    assert trigger.event_name == event_name
    assert trigger.source == expected_source
    assert trigger.label == expected_label
    assert trigger.pull_request_number == expected_number
    assert trigger.head_sha == expected_head_sha


def test_load_trigger_context_from_env_labels_pull_request_synchronize_as_commit_pushed(tmp_path):
    event_path = tmp_path / "pull_request.json"
    event_path.write_text(
        json.dumps(
            {
                "action": "synchronize",
                "pull_request": {
                    "number": 17,
                    "base": {"sha": "abc123"},
                    "head": {"sha": "def456", "repo": {"fork": False}},
                },
            }
        ),
        encoding="utf-8",
    )

    trigger = load_trigger_context_from_env(
        {
            "GITHUB_EVENT_PATH": str(event_path),
            "GITHUB_EVENT_NAME": "pull_request",
            "PR_AGENT_CONTEXT_TRIGGER_EVENT_ACTION": "synchronize",
        }
    )

    assert trigger.label == "commit pushed"


def test_trigger_context_fallback_label_is_readable():
    trigger = TriggerContext(
        event_name="deployment_status",
        action="queued_for_scan",
        source="deployment_status:queued_for_scan",
    )

    assert trigger.label == "deployment status queued for scan"


def test_trigger_context_model_validate_preserves_existing_model_instance():
    original = TriggerContext(
        event_name="pull_request_review",
        action="submitted",
        source="pull_request_review:submitted",
    )

    validated = TriggerContext.model_validate(original)

    assert validated == original
    assert validated.label == "review posted"


def test_trigger_context_populate_label_returns_non_mapping_inputs_unchanged():
    assert TriggerContext._populate_label("already-built") == "already-built"  # noqa: SLF001


@pytest.mark.parametrize(
    ("event_name", "expected_mode"),
    [
        ("pull_request", "ci"),
        ("pull_request_review", "refresh"),
        ("pull_request_review_comment", "refresh"),
        ("workflow_run", "refresh"),
        ("status", "refresh"),
    ],
)
def test_run_config_auto_execution_mode_resolves_by_event_name(
    tmp_path,
    event_name,
    expected_mode,
):
    event_path = tmp_path / f"{event_name}.json"
    if event_name == "pull_request":
        payload = {
            "pull_request": {
                "number": 17,
                "base": {"sha": "abc123"},
                "head": {"sha": "def456", "repo": {"fork": False}},
            }
        }
    elif event_name == "workflow_run":
        payload = {
            "workflow_run": {
                "head_sha": "def456",
                "pull_requests": [{"number": 17}],
            }
        }
    elif event_name == "status":
        payload = {"sha": "def456"}
    else:
        payload = {
            "action": "created",
            "pull_request": {
                "number": 17,
                "base": {"sha": "abc123"},
                "head": {"sha": "def456", "repo": {"fork": False}},
            },
        }
    event_path.write_text(json.dumps(payload), encoding="utf-8")

    config = RunConfig.from_env(
        {
            "GITHUB_REPOSITORY": "shaypal5/example",
            "GITHUB_EVENT_PATH": str(event_path),
            "GITHUB_EVENT_NAME": event_name,
            "GITHUB_RUN_ID": "123",
            "GITHUB_TOKEN": "token",
            "PR_AGENT_CONTEXT_WORKSPACE": str(tmp_path),
            "PR_AGENT_CONTEXT_EXECUTION_MODE": "auto",
        }
    )

    assert config.execution_mode == expected_mode


def test_run_config_repository_property_prefers_explicit_repository_fields(tmp_path):
    config = RunConfig(
        github_token="token",
        repository_owner="shaypal5",
        repository_name="example",
        run_id=1,
        run_attempt=1,
        workspace=tmp_path,
    )

    assert config.repository == "shaypal5/example"


def test_run_config_repository_property_falls_back_to_pull_request(tmp_path):
    config = RunConfig(
        github_token="token",
        pull_request=PullRequestRef(
            owner="shaypal5",
            repo="example",
            number=17,
            base_sha="abc123",
            head_sha="def456",
        ),
        run_id=1,
        run_attempt=1,
        workspace=tmp_path,
    )

    assert config.repository == "shaypal5/example"


def test_run_config_repository_property_returns_empty_without_repository_context(
    tmp_path,
):
    config = RunConfig(
        github_token="token",
        run_id=1,
        run_attempt=1,
        workspace=tmp_path,
    )

    assert config.repository == ""


@pytest.mark.parametrize(
    ("parser", "value", "expected_message"),
    [
        (
            _resolve_execution_mode,
            ("weird", "pull_request"),
            "Unsupported execution mode",
        ),
        (_parse_publish_mode, ("weird",), "Unsupported publish mode"),
        (
            _parse_coverage_selection_strategy,
            ("earliest",),
            "Unsupported coverage selection strategy",
        ),
        (_parse_fork_behavior, ("strict",), "Unsupported fork behavior"),
    ],
)
def test_config_rejects_unsupported_enum_values(parser, value, expected_message):
    with pytest.raises(ValueError, match=expected_message):
        parser(*value)


def test_extract_trigger_context_supports_check_run_and_check_suite():
    check_run = _extract_trigger_context(
        "check_run",
        "completed",
        "check_run:completed",
        {"check_run": {"head_sha": "deadbeef", "pull_requests": [{"number": 17}]}},
    )
    check_suite = _extract_trigger_context(
        "check_suite",
        "completed",
        "check_suite:completed",
        {"check_suite": {"head_sha": "feedface", "pull_requests": [{"number": 18}]}},
    )

    assert check_run.pull_request_number == 17
    assert check_run.head_sha == "deadbeef"
    assert check_suite.pull_request_number == 18
    assert check_suite.head_sha == "feedface"


def test_extract_trigger_context_handles_sparse_refresh_payloads():
    workflow_run = _extract_trigger_context(
        "workflow_run",
        "completed",
        "workflow_run:completed",
        {"workflow_run": {"pull_requests": [{}]}},
    )
    check_run = _extract_trigger_context(
        "check_run",
        "completed",
        "check_run:completed",
        {"check_run": {"head_sha": "deadbeef", "pull_requests": [{}]}},
    )
    check_suite = _extract_trigger_context(
        "check_suite",
        "completed",
        "check_suite:completed",
        {"check_suite": {"head_sha": "feedface", "pull_requests": [{"number": None}]}},
    )
    fallback = _extract_trigger_context("workflow_dispatch", None, "workflow_dispatch", {})
    pull_request = _extract_trigger_context(
        "pull_request",
        "opened",
        "pull_request:opened",
        {
            "pull_request": {
                "number": 17,
                "base": {"sha": "abc123"},
                "head": {"sha": "def456"},
            }
        },
    )

    assert workflow_run.pull_request_number is None
    assert workflow_run.head_sha is None
    assert check_run.pull_request_number is None
    assert check_run.head_sha == "deadbeef"
    assert check_suite.pull_request_number is None
    assert check_suite.head_sha == "feedface"
    assert fallback.event_name == "workflow_dispatch"
    assert fallback.pull_request_number is None
    assert pull_request.is_fork is None


def test_resolve_execution_mode_accepts_explicit_values():
    assert _resolve_execution_mode("ci", "status") == "ci"
    assert _resolve_execution_mode("refresh", "pull_request") == "refresh"


def test_config_private_helpers_cover_sparse_pull_request_mappings():
    assert _extract_pull_request_number_if_present({"pull_request": {"number": 17}}) == 17
    assert _extract_pull_request_number_if_present({}) is None
    assert _extract_shas_from_pull_request_mapping({"base": {}, "head": "oops"}) == (
        None,
        None,
    )

    with pytest.raises(ValueError, match="missing base/head SHAs"):
        _extract_pull_request_shas(
            {"pull_request": {"base": {"sha": "abc123"}, "head": {"sha": ""}}}
        )

    assert _extract_is_fork({"head": "oops"}) is None
    assert _extract_is_fork({"head": {"repo": "oops"}}) is None
    assert _extract_is_fork({"head": {"repo": {"fork": None}}}) is None
    assert _extract_pull_request_shas(
        {"pull_request": {"base": {"sha": "abc123"}, "head": {"sha": "def456"}}}
    ) == ("abc123", "def456")


def test_extract_trigger_context_falls_back_when_refresh_payloads_are_not_mappings():
    workflow_run = _extract_trigger_context(
        "workflow_run",
        "completed",
        "workflow_run:completed",
        {"workflow_run": "invalid"},
    )
    check_run = _extract_trigger_context(
        "check_run",
        "completed",
        "check_run:completed",
        {"check_run": "invalid"},
    )
    check_run_with_empty_list = _extract_trigger_context(
        "check_run",
        "completed",
        "check_run:completed",
        {"check_run": {"head_sha": "deadbeef", "pull_requests": []}},
    )

    assert workflow_run.pull_request_number is None
    assert workflow_run.head_sha is None
    assert check_run.pull_request_number is None
    assert check_run.head_sha is None
    assert check_run_with_empty_list.pull_request_number is None
    assert check_run_with_empty_list.head_sha == "deadbeef"
