---
name: pr-delivery
description: Use when finishing a feature, bugfix, or release-preparation branch in pr-agent-context. This skill covers the last-mile delivery requirement that work is only done after the branch is pushed and a non-draft GitHub PR with a detailed description, appropriate labels, and a milestone when applicable is open.
---

# PR Delivery

Use this playbook when implementation work needs to be finished and published on GitHub.

## When To Use
- The user asks to open a PR.
- The user asks to finish a feature, fix, or release-preparation change.
- The branch already contains the implementation and the remaining work is publication and GitHub metadata.

## Completion Standard
- Do not treat the task as done while the work exists only locally.
- Push the branch.
- Open a non-draft PR with a detailed description.
- Apply repository-appropriate labels.
- Assign a milestone when one fits the work.
- If the label or milestone is missing, create it before closing out the task.

## GitHub Tooling Order
- Use the repo-configured GitHub MCP tools first for PR creation and PR metadata updates.
- Fall back to `gh` only for GitHub operations that the MCP does not support, such as creating a missing label or milestone.

## PR Body Expectations
- Explain the problem or pain point driving the change.
- Summarize the implementation at a reviewer-useful level.
- Call out workflow, docs, skill, or instruction-file updates.
- List validation that was run and any gaps.

## Final Checks
- Confirm the PR is open and not draft.
- Confirm labels and milestone are present.
- Share the PR URL when reporting completion.
