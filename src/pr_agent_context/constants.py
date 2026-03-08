from __future__ import annotations

MANAGED_COMMENT_MARKER = "<!-- pr-agent-context:managed-comment -->"

DEFAULT_PROMPT_OPENING = (
    "Below are the details of unresolved review comments and failing GitHub Actions jobs "
    "on PR #{pr_number}. For each unresolved comment, recommend one of: resolve as "
    "irrelevant, accept and implement the recommended solution, open a separate issue and "
    "resolve as out-of-scope for this PR, accept and implement a different solution, or "
    "resolve as already treated by the code. After I reply with my decision per item, "
    "implement the accepted actions, resolve the corresponding PR comments, fix each failing "
    "job below, and push all of these changes in a single commit."
)

COPILOT_COMMENT_SECTION = "Copilot Comments"
REVIEW_COMMENT_SECTION = "Other Review Comments"
FAILING_JOBS_SECTION = "Failing Jobs"
PATCH_COVERAGE_SECTION = "Codecov/patch"

DEFAULT_PROMPT_TEMPLATE = """
{{ prompt_preamble }}

{{ opening_instructions }}

{{ copilot_comments_section }}

{{ review_comments_section }}

{{ failing_jobs_section }}

{{ patch_coverage_section }}
"""

SUPPORTED_TEMPLATE_PLACEHOLDERS = (
    "pr_number",
    "prompt_preamble",
    "opening_instructions",
    "copilot_comments_section",
    "review_comments_section",
    "failing_jobs_section",
    "patch_coverage_section",
)

DEFAULT_COPILOT_AUTHOR_PATTERNS = (
    "copilot-pull-request-reviewer[bot]",
    "github-copilot[bot]",
    "re:copilot.*bot",
)

FAILED_JOB_CONCLUSIONS = {"failure", "timed_out", "startup_failure"}
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
DEFAULT_MAX_LOG_LINES_PER_JOB = 80
DEFAULT_TARGET_PATCH_COVERAGE = 100.0
DEFAULT_COVERAGE_ARTIFACT_PREFIX = "pr-agent-context-coverage"
DEFAULT_TOOL_REF = "v1"
DEFAULT_DEBUG_ARTIFACT_PREFIX = "pr-agent-context-debug"

DEFAULT_SECTION_BUDGETS = {
    "copilot_comments_section": 12000,
    "review_comments_section": 12000,
    "failing_jobs_section": 14000,
    "patch_coverage_section": 12000,
}

DEFAULT_ROOT_COMMENT_BODY_CHARS = 2400
DEFAULT_REPLY_BODY_CHARS = 700
DEFAULT_FAILURE_EXCERPT_CHARS = 2600
DEFAULT_FAILURE_EXCERPT_MIN_LINES = 6
DEFAULT_FAILURE_EXCERPT_MAX_LINES = 40
DEFAULT_PATCH_SECTION_HARD_LIMIT = 20000
