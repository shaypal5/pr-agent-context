---
name: refresh-lifecycle
description: Use when working on pr-agent-context refresh-mode workflow design, trigger selection, merged or closed PR refresh behavior, same-repo guards, or comment refresh lifecycle guidance.
---

# Refresh Lifecycle

Use this playbook for refresh-mode workflow design and debugging in `pr-agent-context`.

## When To Use
- The user asks how refresh-mode workflows should be wired in this repo or downstream repos.
- The user asks why a refresh did or did not run after a review, status, or check signal.
- The user asks about merged or closed PR behavior for later check-driven refreshes.

## Preferred Refresh Shape
- Use a separate refresh workflow instead of rerunning the full CI producer workflow.
- Default to:
  - `execution_mode: refresh`
  - `publish_mode: append`
  - `publish_all_clear_comments_in_refresh: false`
  - `enable_cross_run_coverage_lookup: true`
  - `wait_for_reviews_to_settle: true` when review timing matters
- Prefer same-repo guards before comment mutation.
- Prefer per-PR concurrency so bursts of review/check activity collapse to the newest run.
- When bot-authored review events are approval-gated, prefer a repo-owned `schedule` that
  redispatches the refresh workflow through `workflow_dispatch` with explicit PR number/base/head
  overrides instead of relying only on the blocked event-triggered run.
- Guard scheduled redispatches with a bounded lookup for recent same-head refresh managed comments;
  do not blindly redispatch every open PR on every tick.
- Avoid staleness heuristics based on `pull.updated_at`, because publishing the managed comment can
  perturb that timestamp and cause self-invalidating redispatch decisions.
- When refresh runs suppress all-clear comments, comment presence alone is not a sufficient skip
  signal. Add a second dedupe guard for recent or in-flight scheduled `workflow_dispatch` runs for
  the same PR number and head SHA.
- Prefer leaving `cancel-in-progress` enabled for direct event-triggered refreshes while disabling
  cancel-on-rerun for scheduled `workflow_dispatch` refreshes so the fallback path does not churn
  in-flight runs.

## Trigger Guidance
- Core review triggers: `pull_request_review`, `pull_request_review_comment`
- Check-related triggers: `status`, `check_run`, `check_suite`
- For approval-gated bot reviews, add `workflow_dispatch` plus a `schedule` fanout job that
  dispatches same-repo open PR refreshes with explicit PR context.
- Scheduled fanout jobs should inspect a bounded window of recent comments and skip dispatch when a
  same-head refresh managed comment already exists.
- Add per-PR error isolation so one API failure does not block dispatch decisions for every other
  open PR.
- Ignore GitHub Actions-originated `check_run` events unless the task explicitly wants self-observation.
- When refresh runs reuse prior CI coverage artifacts, point `coverage_source_workflows` at the CI producer workflow name.

## Merged Or Closed PR Behavior
- Later `status`, `check_run`, or `check_suite` events may arrive after the PR is closed or merged.
- In those paths, `pr-agent-context` resolves the PR from the trigger SHA and can preserve the trigger head SHA even when GitHub returns a closed PR whose stored head SHA differs.
- When debugging this behavior, inspect `pull-request-context.json` before assuming resolution is wrong.

## Docs And Example Ripple
- If refresh recommendations change, update:
  - `README.md` refresh guidance
  - `examples/pr-agent-context-refresh.yml`
  - `AGENTS.md`
  - any downstream-integration guidance that repeats refresh defaults
