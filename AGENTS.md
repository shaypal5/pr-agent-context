# Repository Instructions

- Always use the repo-specific git and GitHub MCPs configured in the environment. Only fall back to the corresponding CLIs for actions the MCPs do not support.
- Start every new feature, milestone, bugfix, or other non-trivial implementation branch from up-to-date `main`.
- Before beginning new implementation work, update local `main` from `origin/main` and branch from that refreshed tip.
- Do not continue new work on an already-merged feature branch unless the user explicitly asks for that.

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
- Refresh runs should use scoped comment updates and suppress no-op all-clear comments.
- The current recommended refresh pattern is `publish_mode: update_latest_scoped` with `publish_all_clear_comments_in_refresh: false`.

### Update Ripple Checklist
- If you change reusable workflow inputs, config parsing, or environment variable handling, update the README input docs, examples, config tests, and this repo's self-consumer workflows when relevant.
- If you change rendered comment wording or metadata, update [`tests/fixtures/prompts/expected_comment.md`](tests/fixtures/prompts/expected_comment.md) and any render/version assertions that pin the output.
- If you change patch coverage behavior, keep branch coverage at `100%` and update the service/debug tests that lock in the behavior.

### Release Flow
- Release bump PRs update exactly these files: [`pyproject.toml`](pyproject.toml), [`tests/test_version.py`](tests/test_version.py), and [`tests/fixtures/prompts/expected_comment.md`](tests/fixtures/prompts/expected_comment.md).
- Open release bump PRs from refreshed `main`.
- After the release PR is merged, tag the merged `main` commit with `vX.Y.Z`.
- Let `release-tags.yml` move the `v4` major tag; do not retag a PR branch tip.

### GitHub Tooling
- Keep using the repo-specific git and GitHub MCPs first.
- Use `gh api` only for gaps the MCPs do not cover yet, such as resolving review threads.

### Repo-Local Skills
- For release/version work, consult [`skills/release-flow/SKILL.md`](skills/release-flow/SKILL.md).
- For downstream/client repo integration or patch-coverage wiring/debugging, consult [`skills/downstream-integration/SKILL.md`](skills/downstream-integration/SKILL.md).
