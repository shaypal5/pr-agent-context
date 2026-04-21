# Repository Instructions

- Always use the repo-specific git and GitHub MCPs configured in the environment. Only fall back to the corresponding CLIs for actions the MCPs do not support.
- Start every new feature, milestone, bugfix, or other non-trivial implementation branch from up-to-date `main`.
- Before beginning new implementation work, update local `main` from `origin/main` and branch from that refreshed tip.
- Do not continue new work on an already-merged feature branch unless the user explicitly asks for that.
- Treat feature or PR work as incomplete until a non-draft GitHub PR is open from the working branch with a detailed description, appropriate labels, and a milestone when one applies.

## Repo Playbook

### Key Workflows
- Self-consumer CI lives in [`.github/workflows/ci.yml`](.github/workflows/ci.yml).
- Self-refresh behavior lives in [`.github/workflows/pr-agent-context-refresh.yml`](.github/workflows/pr-agent-context-refresh.yml).
- The reusable workflow contract lives in [`.github/workflows/pr-agent-context.yml`](.github/workflows/pr-agent-context.yml).
- Release tag promotion lives in [`.github/workflows/release-tags.yml`](.github/workflows/release-tags.yml).

### Coverage Modes
- `raw_coverage_artifacts` is still the default and is the right fit for matrix or otherwise complex coverage producers.
- `coverage_xml_artifact` is the simple-repo mode for a caller-provided combined `coverage.xml`; in that mode `pr-agent-context` suppresses Codecov external checks and statuses from its own failing-check collection.
- Do not treat the Codecov UI or Codecov comment text as the tool's source of truth. Patch coverage comes from the downloaded artifacts and the repo diff.

### Refresh Lifecycle Defaults
- CI runs may publish an all-clear comment.
- Refresh runs should default to append-mode comment history and suppress no-op all-clear comments.
- The current recommended refresh pattern is `publish_mode: append` with `publish_all_clear_comments_in_refresh: false`.
- If bot-authored review events can leave refresh runs stuck in approval, prefer a repo-owned `schedule` -> `workflow_dispatch` fallback that passes explicit PR context overrides.

### Update Ripple Checklist
- If you change reusable workflow inputs, config parsing, or environment variable handling, update the README input docs, examples, config tests, and this repo's self-consumer workflows when relevant.
- If you change prompt-template placeholders or rendered sections, update the README placeholder list, the example template, render tests, and any repo-local skills that describe template evolution.
- If you change refresh lifecycle behavior or trigger recommendations, update the README refresh guidance, [`examples/pr-agent-context-refresh.yml`](examples/pr-agent-context-refresh.yml), and any repo-local skills that cover refresh wiring.
- If you change rendered comment wording or metadata, update [`tests/fixtures/prompts/expected_comment.md`](tests/fixtures/prompts/expected_comment.md) and any render/version assertions that pin the output.
- If you change patch coverage behavior, keep branch coverage at `100%` and update the service/debug tests that lock in the behavior.
- If you change raw coverage artifact discovery or path normalization behavior, update the README coverage contract and downstream integration guidance.

### Release Flow
- Release bump PRs update exactly these files: [`pyproject.toml`](pyproject.toml), [`tests/test_version.py`](tests/test_version.py), and [`tests/fixtures/prompts/expected_comment.md`](tests/fixtures/prompts/expected_comment.md).
- Open release bump PRs from refreshed `main`.
- After the release PR is merged, tag the merged `main` commit with `vX.Y.Z`.
- Let `release-tags.yml` move the `v4` major tag; do not retag a PR branch tip.

### PR Completion
- For feature, fix, and release-PR work, do not stop at local commits.
- Push the branch and open a non-draft PR on GitHub with a detailed description before considering the task done, unless the user explicitly says not to.
- Apply repository-appropriate labels and assign a milestone when one fits the work.
- If the required label or milestone does not exist yet, create it.

### GitHub Tooling
- Keep using the repo-specific git and GitHub MCPs first.
- Use `gh api` only for gaps the MCPs do not cover yet, such as resolving review threads.

### Repo-Local Skills
- For release/version work, consult [`skills/release-flow/SKILL.md`](skills/release-flow/SKILL.md).
- For refresh workflow design, merged/closed PR refresh behavior, or trigger selection, consult [`skills/refresh-lifecycle/SKILL.md`](skills/refresh-lifecycle/SKILL.md).
- For managed comment publication behavior and append/update mode decisions, consult [`skills/comment-publishing/SKILL.md`](skills/comment-publishing/SKILL.md).
- For downstream/client repo integration or patch-coverage wiring/debugging, consult [`skills/downstream-integration/SKILL.md`](skills/downstream-integration/SKILL.md).
- For prompt template and rendered-section changes, consult [`skills/prompt-template-evolution/SKILL.md`](skills/prompt-template-evolution/SKILL.md).
- For finishing feature work and publishing a ready PR, consult [`skills/pr-delivery/SKILL.md`](skills/pr-delivery/SKILL.md).
