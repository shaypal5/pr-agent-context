# pr-agent-context

`pr-agent-context` is a reusable GitHub Actions tool that assembles a single managed
PR handoff comment for coding agents.

Current behavior includes:

- unresolved PR review threads
- same-run failed GitHub Actions jobs with trimmed log excerpts
- patch coverage analysis from raw `coverage.py` artifacts
- uncovered changed executable Python lines for the PR diff
- deterministic prompt rendering with stable item IDs
- single managed PR comment upsert/delete using a hidden HTML marker

The managed comment body shape is:

````markdown
<!-- pr-agent-context:managed-comment -->
```markdown
<rendered prompt>
```
````

## Downstream Usage

Short reusable-workflow usage:

```yaml
jobs:
  pr-agent-context:
    name: PR agent context
    if: ${{ always() && github.event_name == 'pull_request' }}
    needs: [test, lint]
    permissions:
      contents: read
      actions: read
      pull-requests: write
    uses: shaypal5/pr-agent-context/.github/workflows/pr-agent-context.yml@v1
    with:
      target_patch_coverage: "100"
      include_patch_coverage: true
      coverage_artifact_prefix: pr-agent-context-coverage
```

The reusable workflow keeps Milestone 1 defaults and adds these Milestone 2 inputs:

- `target_patch_coverage`: required patch coverage percentage, default `"100"`
- `include_patch_coverage`: enable patch coverage analysis, default `true`
- `coverage_artifact_prefix`: artifact name prefix for raw `.coverage*` uploads, default `pr-agent-context-coverage`
- `force_patch_coverage_section`: render the `# Codecov/patch` section even when patch coverage is not actionable, default `false`

## Coverage Artifact Contract

Patch coverage is computed locally from:

1. the caller repo git diff between the PR base SHA and head SHA
2. merged raw `.coverage*` data downloaded from artifacts produced earlier in the same caller workflow run

The reusable workflow does **not** scrape the Codecov UI and does **not** call Codecov APIs.

Coverage-producing jobs in downstream repos must upload raw `coverage.py` data files in
artifacts whose names start with the configured prefix. Example:

```yaml
jobs:
  test:
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest, macos-latest]
        python-version: ["3.11", "3.12"]
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python-version }}
      - run: python -m pip install -e ".[dev]"
      - run: pytest
      - name: Upload raw coverage data
        if: always()
        uses: actions/upload-artifact@v4
        with:
          name: pr-agent-context-coverage-${{ matrix.os }}-py${{ matrix.python-version }}
          path: .coverage*
          if-no-files-found: ignore
```

## Patch Coverage Rules

- Only changed Python files are considered.
- Added or modified line numbers come from `git diff --unified=0 <base>...<head>` in the checked-out caller repo.
- The denominator is changed executable Python lines only.
- Changed non-executable lines do not count against coverage.
- If a changed Python file is in coverage scope but has no measured data, its changed executable lines are treated as uncovered.
- If there are no changed executable Python lines, patch coverage is treated as `N/A`.
- The `# Codecov/patch` section is omitted for `N/A` patches unless `force_patch_coverage_section` is enabled.
- Uncovered lines are rendered as explicit line lists rather than compressed ranges so the output is easy to hand to a coding agent.

## Coverage Path Caveats

Cross-matrix coverage merging works best when downstream repos normalize paths in their
coverage configuration. Recommended guidance:

- enable `relative_files = true`
- define `[paths]` mappings when different runners produce different absolute prefixes
- keep coverage source configuration explicit when practical

Example `pyproject.toml` guidance for a downstream repo:

```toml
[tool.coverage.run]
relative_files = true
source = ["src"]

[tool.coverage.paths]
source = [
  "src",
  "/home/runner/work/my-repo/my-repo/src",
  "/Users/runner/work/my-repo/my-repo/src",
]
```

If a repo uses unusual layout or path rewriting, `pr-agent-context` will still merge the
available `.coverage*` files locally, but patch coverage quality depends on the caller
repo's `coverage.py` path normalization being sane.
