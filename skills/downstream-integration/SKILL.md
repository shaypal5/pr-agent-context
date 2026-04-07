# Downstream Integration

Use this playbook for client-repo adoption, refresh workflow fixes, and patch-coverage integration/debugging.

## When To Use
- The user asks how a downstream repo should wire `pr-agent-context`.
- The user asks why a client repo did or did not refresh after later PR signals.
- The user asks why patch coverage disagrees with Codecov or appears unavailable.

## Choose The Coverage Mode
- Use `raw_coverage_artifacts` for matrix or otherwise complex producers that need raw `.coverage*` files.
- Use `coverage_xml_artifact` for simpler repos that already produce a combined `coverage.xml` artifact in CI.
- In XML mode, set all three in both the initial PR workflow and the refresh workflow:
  - `patch_coverage_source_mode: coverage_xml_artifact`
  - `coverage_report_artifact_name: <artifact-name>`
  - `coverage_report_filename: coverage.xml`
- In XML mode, `pr-agent-context` suppresses Codecov external checks/statuses automatically.

## Refresh Workflow Defaults
- Use a separate refresh workflow rather than retriggering the full CI producer workflow.
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

## Coverage Debugging Rules
- Treat artifacts and debug bundles as the source of truth, not the Codecov UI timing state.
- A missing or still-processing Codecov result does not prove local patch coverage is wrong.
- For repo-shape/path issues, inspect the caller's `coverage.py` config (`relative_files`, `source`, `paths`) and compare it to the artifact mode in use.

## Useful Repo References
- `README.md` documents both raw artifact mode and combined XML mode.
- `.github/workflows/ci.yml` shows the current self-consumer raw artifact pattern.
- `.github/workflows/pr-agent-context-refresh.yml` shows the current self-refresh pattern.
