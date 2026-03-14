# Release Flow

Use this playbook for version bump PRs and post-merge release tagging in `pr-agent-context`.

## When To Use
- The user asks for a version bump PR.
- The user asks to release a merged version by tagging `main`.

## Release Bump PR
1. Start from updated `main`.
2. Create a fresh `codex/release-vX.Y.Z` branch.
3. Update:
   - `pyproject.toml`
   - `tests/test_version.py`
   - `tests/fixtures/prompts/expected_comment.md`
4. Validate with:
   - `pytest tests/test_version.py tests/test_render.py -q`
   - `ruff check tests/test_version.py`
5. Open the PR with title `Bump version to X.Y.Z`.

## Post-Merge Tagging
1. Check out `main`.
2. Run `git pull --ff-only origin main`.
3. Tag the merged tip with `vX.Y.Z`.
4. Push the tag to `origin`.
5. Rely on `.github/workflows/release-tags.yml` to move the matching `v4` major tag.

## Common Mistakes To Avoid
- Do not tag a feature branch or an unmerged release PR commit.
- Do not forget the rendered comment fixture; version bumps change the reported tool version.
- Do not skip refreshing `main` before either the PR or the final tag.
