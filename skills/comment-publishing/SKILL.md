---
name: comment-publishing
description: Use when changing or explaining pr-agent-context managed comment publication behavior, including append versus update modes, hiding superseded comments, all-clear suppression, and informational-only sections that should not count as actionable failures.
---

# Comment Publishing

Use this playbook for managed PR comment publication behavior in `pr-agent-context`.

## When To Use
- The user asks how managed comments should be created, updated, hidden, or suppressed.
- The user asks whether refresh runs should append or mutate prior comments.
- The user changes actionable-versus-informational rendering behavior and needs publication guidance.

## Default Policy
- CI runs may publish all-clear comments.
- Refresh runs should default to:
  - `publish_mode: append`
  - `publish_all_clear_comments_in_refresh: false`
- `append` is the recommended default because it preserves refresh snapshots while the default hiding behavior keeps the newest result visible.
- Use `update_latest_scoped` only when the caller explicitly prefers rewriting one refresh-scoped comment in place.

## Actionable Versus Informational Signals
- Approval-gated Actions runs are informational by default.
- Informational-only sections should not count as failing checks or trigger publication by themselves.
- If a new section changes whether a run is actionable, check render tests and comment publication logic together.

## Docs And Fixture Ripple
- If comment publication policy changes, update:
  - `README.md`
  - `AGENTS.md`
  - `tests/fixtures/prompts/expected_comment.md` when wrapper wording or metadata changes
  - render and issue-comment tests that pin publication decisions
