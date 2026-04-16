from __future__ import annotations

MANAGED_COMMENT_MARKER_PREFIX = "<!-- pr-agent-context:managed-comment"
MANAGED_COMMENT_SCHEMA_VERSION = "v5"

DEFAULT_ALL_CLEAR_PROMPT = (
    "No unresolved review comments, failing checks, or actionable patch "
    "coverage gaps were found on PR #{pr_number}. Treat this PR as all clear "
    "unless new signals appear."
)

DEFAULT_REFRESH_NOTE = "This is a refreshed snapshot of the current PR state."

COPILOT_COMMENT_SECTION = "Copilot Comments"
REVIEW_COMMENT_SECTION = "Other Review Comments"
FAILING_CHECKS_SECTION = "Failing Checks"
APPROVAL_GATED_ACTIONS_RUNS_SECTION = "Approval-Gated Actions Runs"
PATCH_COVERAGE_SECTION = "Patch coverage"

DEFAULT_PROMPT_TEMPLATE = """
{{ prompt_preamble }}

{{ opening_instructions }}

{{ copilot_comments_section }}

{{ review_comments_section }}

{{ failing_checks_section }}

{{ approval_gated_actions_run_notes_section }}

{{ patch_coverage_section }}
"""

SUPPORTED_TEMPLATE_PLACEHOLDERS = (
    "pr_number",
    "prompt_preamble",
    "opening_instructions",
    "copilot_comments_section",
    "review_comments_section",
    "failing_checks_section",
    "approval_gated_actions_run_notes_section",
    "patch_coverage_section",
)

DEFAULT_COPILOT_AUTHOR_PATTERNS = (
    "copilot-pull-request-reviewer[bot]",
    "github-copilot[bot]",
    "re:copilot.*bot",
)
DEFAULT_INCLUDE_OUTDATED_REVIEW_THREADS = True

FAILED_JOB_CONCLUSIONS = {"failure", "timed_out", "startup_failure"}
FAILED_CHECK_CONCLUSIONS = {
    "failure",
    "timed_out",
    "startup_failure",
    "action_required",
}
FAILED_STATUS_STATES = {"error", "failure"}
ACTIONS_APP_NAMES = {"github-actions", "GitHub Actions"}
ERROR_MARKERS = (
    "::error",
    "Traceback",
    "FAILED",
    "AssertionError",
    "E   ",
    "Error:",
    "Exception:",
)

DEFAULT_MAX_REVIEW_THREADS = 50
DEFAULT_MAX_FAILED_JOBS = 20
DEFAULT_MAX_FAILED_RUNS = 20
DEFAULT_MAX_EXTERNAL_CHECKS = 20
DEFAULT_MAX_FAILING_ITEMS = 25
DEFAULT_MAX_LOG_LINES_PER_JOB = 80
DEFAULT_INCLUDE_FAILED_STEP_OUTPUT = True
DEFAULT_MAX_FAILED_STEP_OUTPUT_LINES = 500
DEFAULT_CHECK_SETTLE_TIMEOUT_SECONDS = 45
DEFAULT_CHECK_SETTLE_POLL_INTERVAL_SECONDS = 5
DEFAULT_REVIEW_SETTLE_TIMEOUT_SECONDS = 180
DEFAULT_REVIEW_SETTLE_POLL_INTERVAL_SECONDS = 10
DEFAULT_CHARACTERS_PER_LINE = 100
DEFAULT_TARGET_PATCH_COVERAGE = 100.0
DEFAULT_COVERAGE_ARTIFACT_PREFIX = "pr-agent-context-coverage"
DEFAULT_TOOL_REF = "v4"
DEFAULT_DEBUG_ARTIFACT_PREFIX = "pr-agent-context-debug"
DEFAULT_EXECUTION_MODE = "auto"
DEFAULT_PUBLISH_MODE = "append"
DEFAULT_PUBLISH_ALL_CLEAR_COMMENTS_IN_REFRESH = False
DEFAULT_INCLUDE_APPROVAL_GATED_ACTIONS_RUN_NOTES = False
DEFAULT_COVERAGE_SELECTION_STRATEGY = "latest_successful"
DEFAULT_FORK_BEHAVIOR = "best_effort"
DEFAULT_COVERAGE_SOURCE_CONCLUSIONS = ("success",)
DEFAULT_PATCH_COVERAGE_SOURCE_MODE = "raw_coverage_artifacts"
DEFAULT_COVERAGE_REPORT_FILENAME = "coverage.xml"

DEFAULT_SECTION_BUDGETS = {
    "copilot_comments_section": 12000,
    "review_comments_section": 12000,
    "failing_checks_section": 16000,
    "approval_gated_actions_run_notes_section": 12000,
    "patch_coverage_section": 12000,
}

DEFAULT_ITEM_BUDGET_FLOOR = 200
DEFAULT_ROOT_COMMENT_BODY_CHARS = 2400
DEFAULT_REPLY_BODY_CHARS = 700
DEFAULT_FAILURE_EXCERPT_CHARS = 2600
DEFAULT_FAILURE_EXCERPT_MAX_LINES = 40
DEFAULT_PATCH_SECTION_HARD_LIMIT = 20000
