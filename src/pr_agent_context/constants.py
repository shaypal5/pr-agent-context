from __future__ import annotations

import re

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

COPILOT_AUTHOR_LOGINS = {
    "copilot-pull-request-reviewer[bot]",
    "github-copilot[bot]",
}
COPILOT_AUTHOR_PATTERNS = (re.compile(r"copilot.*bot", re.IGNORECASE),)

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
