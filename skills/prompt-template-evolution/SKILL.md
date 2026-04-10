---
name: prompt-template-evolution
description: Use when adding, removing, or reshaping pr-agent-context rendered prompt sections or prompt-template placeholders, especially when README docs, example templates, fixtures, and render tests must stay in sync.
---

# Prompt Template Evolution

Use this playbook when the rendered prompt contract changes in `pr-agent-context`.

## When To Use
- The user adds or removes a rendered section.
- The user changes prompt-template placeholders or default-template behavior.
- The user updates custom-template guidance or example templates.

## Required Ripple Checks
- Update the README placeholder list.
- Update the README custom-template example when section ordering or placeholders change.
- Update `examples/pr-agent-context-template.md`.
- Update render tests that exercise custom templates or omitted-placeholder behavior.
- Update any repo-local skills or playbook notes that mention template evolution.

## Placement Rules
- If a section is optional but should still render when enabled, document what happens when a custom template omits its placeholder.
- Preserve backward compatibility for older templates when a section is split into multiple placeholders. For example, if Copilot comments move into their own placeholder, legacy templates that still render the generic review-comments placeholder should continue to surface them deterministically.
- Keep the default behavior deterministic and explicitly describe fallback placement in docs.
- Treat template-contract changes as API changes for downstream consumers.

## Keep The Scope Tight
- Use placeholders only for top-level rendered sections or stable metadata values.
- Do not expand the template contract casually; every new placeholder increases downstream maintenance cost.
