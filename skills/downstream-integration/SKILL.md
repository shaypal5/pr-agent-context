# Downstream Integration

Use this playbook for client-repo adoption, refresh workflow fixes, and patch-coverage integration/debugging.

## When To Use
- The user asks how a downstream repo should wire `pr-agent-context`.
- The user asks why a client repo did or did not refresh after later PR signals.
- The user asks why patch coverage disagrees with Codecov or appears unavailable.
- The user asks how custom prompt templates should evolve when new optional sections are added.

## Choose The Coverage Mode
- Use `raw_coverage_artifacts` for matrix or otherwise complex producers that need raw `.coverage*` files.
- Use `coverage_xml_artifact` for simpler repos that already produce a combined `coverage.xml` artifact in CI.
- In raw-artifact mode, same-named `.coverage` files may live in separate artifact directories; the
  reusable workflow preserves those directories and discovers coverage files recursively.
- In XML mode, set all three in both the initial PR workflow and the refresh workflow:
  - `patch_coverage_source_mode: coverage_xml_artifact`
  - `coverage_report_artifact_name: <artifact-name>`
  - `coverage_report_filename: coverage.xml`
- In XML mode, `pr-agent-context` suppresses Codecov external checks/statuses automatically.

## Refresh Workflow Defaults
- Use a separate refresh workflow rather than retriggering the full CI producer workflow.
- Supported refresh signals include `pull_request_review`, `pull_request_review_comment`, `status`,
  `check_run`, and `check_suite`; `status` / `check_run` / `check_suite` are the relevant hooks
  when later check activity arrives after the original CI run is gone.
- Recommended refresh settings:
  - `execution_mode: refresh`
  - `publish_mode: append`
  - `publish_all_clear_comments_in_refresh: false`
  - `enable_cross_run_coverage_lookup: true`
  - `wait_for_reviews_to_settle: true` when review timing matters
- `append` keeps a comment-per-refresh trail while the default append-mode hiding behavior minimizes
  older managed comments so the newest refresh result stays visible.
- Use `update_latest_scoped` only if the caller explicitly prefers mutating one refresh-scoped
  comment in place over preserving each refresh snapshot.
- Leave approval-gated Actions runs hidden by default. Only enable `include_approval_gated_actions_run_notes: true` if the caller explicitly wants a separate informational note for maintainer-approval waits.
- Point `coverage_source_workflows` at the producer workflow name when refresh runs need to reuse earlier CI artifacts.
- Prefer same-repo guards and per-PR concurrency in refresh workflows so comment mutation is
  limited to writable events and noisy bursts coalesce cleanly.
- If Copilot or another bot can trigger approval-gated review events in the caller repo, add a
  repo-owned `schedule` job that redispatches the refresh workflow through `workflow_dispatch`
  with explicit PR number/base/head overrides.
- Closed or merged PR refreshes can resolve from the trigger SHA; when debugging those paths,
  inspect `pull-request-context.json` before assuming GitHub returned the wrong PR.

## Coverage Debugging Rules
- Treat artifacts and debug bundles as the source of truth, not the Codecov UI timing state.
- A missing or still-processing Codecov result does not prove local patch coverage is wrong.
- For repo-shape/path issues, inspect the caller's `coverage.py` config (`relative_files`, `source`, `paths`) and compare it to the artifact mode in use.
- Split-checkout absolute paths are rebased onto the active workspace when possible, but callers
  should still prefer `relative_files = true` and explicit `[paths]` mappings over relying on
  best-effort path rebasing.

## Prompt Template Notes
- When a caller enables `include_approval_gated_actions_run_notes`, their custom template should
  include `{{ approval_gated_actions_run_notes_section }}` if they want explicit placement of that
  informational section.
- If the placeholder is omitted, the section is appended after the rendered template.

## Useful Repo References
- `README.md` documents both raw artifact mode and combined XML mode.
- `.github/workflows/ci.yml` shows the current self-consumer raw artifact pattern.
- `.github/workflows/pr-agent-context-refresh.yml` shows the current self-refresh pattern.
