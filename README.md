# pr-agent-context

`pr-agent-context` is a reusable GitHub Actions tool that assembles managed
PR handoff comments for coding agents.

Current behavior includes:

- unresolved PR review threads
- PR-wide failing-check aggregation for the PR head SHA
- failed GitHub Actions workflow runs/jobs with trimmed log excerpts
- failed external check runs and commit statuses when GitHub exposes useful metadata
- patch coverage analysis from raw `coverage.py` artifacts
- uncovered changed executable Python lines for the PR diff
- configurable prompt templating from the caller repository
- structured debug artifacts for inspection and downstream automation
- deterministic prompt rendering with stable item IDs
- append-only managed PR comments scoped to the producing workflow run

Each run writes its own managed comment. The tool updates only the comment for the exact same
`(run_id, run_attempt)` identity and preserves older run comments for auditability.

The managed comment body shape is:

````markdown
<!-- pr-agent-context:managed-comment; schema=v3; pr=<PR>; run_id=<RUN_ID>; run_attempt=<ATTEMPT>; head_sha=<HEAD_SHA>; tool_ref=<TOOL_REF> -->
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
    uses: shaypal5/pr-agent-context/.github/workflows/pr-agent-context.yml@v2
    with:
      target_patch_coverage: "100"
      include_patch_coverage: true
      coverage_artifact_prefix: pr-agent-context-coverage
      prompt_template_file: .github/pr-agent-context-template.md
      debug_artifacts: true
```

Versioning guidance:

- use `@v2` in downstream repositories for the stable major line
- publish fixed release tags such as `v2.0.0` for exact version pinning
- this repository includes [`.github/workflows/release-tags.yml`](/.github/workflows/release-tags.yml), which automatically moves `v2` when a `v2.x.y` tag is pushed

Example release flow in this repository:

```bash
git checkout main
git pull --ff-only origin main
git tag v2.0.1
git push origin v2.0.1
```

After the version tag push completes, the workflow force-updates the matching major tag to the same commit.

The reusable workflow inputs are:

- `tool_ref`: ref of `shaypal5/pr-agent-context` to run, default `"v2"`
- `include_review_comments`: include unresolved PR review threads, default `true`
- `include_failing_checks`: include failing checks in the rendered prompt, default `true`
- `include_cross_run_failures`: expand Actions failure collection from the current run to PR-head-SHA-wide failed runs/jobs, default `true`
- `include_external_checks`: include failed external check runs and commit statuses for the PR head SHA, default `true`
- `wait_for_checks_to_settle`: briefly poll the PR head SHA check universe so late-arriving checks can appear before collection, default `true`
- `target_patch_coverage`: required patch coverage percentage, default `"100"`
- `include_patch_coverage`: enable patch coverage analysis, default `true`
- `coverage_artifact_prefix`: artifact name prefix for raw `.coverage*` uploads, default `pr-agent-context-coverage`
- `force_patch_coverage_section`: render the `# Codecov/patch` section even when patch coverage is not actionable, default `false`
- `prompt_preamble`: optional text inserted near the top of the rendered prompt
- `prompt_template_file`: optional template file path in the caller repository workspace
- `copilot_author_patterns`: comma- or newline-separated exact logins and `re:` regexes for Copilot actor matching
- `debug_artifacts`: upload JSON/markdown debug artifacts, default `true`
- `debug_artifact_prefix`: artifact name prefix for uploaded debug bundles, default `pr-agent-context-debug`
- `max_review_threads`: cap unresolved review threads, default `50`
- `max_actions_runs`: cap completed workflow runs inspected for the PR head SHA, default `20`
- `max_actions_jobs`: cap failed Actions jobs included after aggregation and dedupe, default `20`
- `max_external_checks`: cap failed external check/status items included after normalization, default `20`
- `max_failing_checks`: cap the total rendered failing-check items after dedupe, default `25`
- `max_log_lines_per_job`: cap collected failed-job excerpt lines before rendering, default `80`
- `check_settle_timeout_seconds`: maximum seconds to wait for the head-SHA check universe to settle, default `45`
- `check_settle_poll_interval_seconds`: polling interval in seconds while waiting for late checks, default `5`
- `characters_per_line`: wrap plain prose lines in rendered output to this width, default `100`

When failing-check collection is enabled, `pr-agent-context` now waits briefly for the PR head
SHA check universe to settle before collecting failures. This helps late-arriving external checks
such as Codecov appear in the rendered prompt instead of being missed because they posted a few
seconds after the workflow started.

## Prompt Templating

If `prompt_template_file` is not provided, `pr-agent-context` uses the built-in default
template. If it is provided, the file is loaded from the caller repository workspace and
rendered deterministically with a small placeholder renderer.

Supported placeholders:

- `{{ pr_number }}`
- `{{ prompt_preamble }}`
- `{{ opening_instructions }}`
- `{{ copilot_comments_section }}`
- `{{ review_comments_section }}`
- `{{ failing_checks_section }}`
- `{{ patch_coverage_section }}`

Unknown placeholders and malformed template braces fail fast with a clear validation error.

Example custom template file:

```markdown
{{ prompt_preamble }}

# PR {{ pr_number }}

{{ opening_instructions }}

{{ copilot_comments_section }}
{{ review_comments_section }}
{{ failing_checks_section }}
{{ patch_coverage_section }}
```

`prompt_preamble`, when non-empty, is always inserted near the top in a deterministic way,
even if a custom template omits the explicit placeholder.

Rendered output also applies a configurable prose-wrapping pass. By default, plain prose lines
are wrapped to 100 characters, while semantically sensitive lines are left untouched:

- fenced code blocks
- indented/code excerpt lines
- headings
- list items
- metadata lines such as `Location:` and `URL:`
- lines containing URLs

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
          include-hidden-files: true
          if-no-files-found: ignore
```

## Debug Artifacts

When `debug_artifacts` is enabled, the reusable workflow uploads a debug bundle containing:

- `collected-context.json`: normalized collected review/failure/coverage context
- `failing-check-universe.json`: raw failing-check universe, deduped failing-check set, source counts, and collector warnings
- `prompt.md`: rendered prompt markdown before the managed-comment wrapper
- `comment-body.md`: final managed PR comment body
- `comment-sync.json`: parsed managed comments, current run identity, selected sync target, and publication action
- `summary.json`: a compact machine-readable summary with counts, booleans, prompt SHA, template diagnostics, and truncation notes

These artifacts are intended for maintainers and downstream automation. They are deterministic
JSON/markdown outputs and are not redacted beyond standard secret handling in the workflow
environment.

## PR-Wide Failing Checks

Failing-check collection is PR-wide by default rather than limited to the reusable workflow's
own run.

For the PR head SHA, `pr-agent-context` will attempt to collect:

- failed GitHub Actions workflow runs and failed jobs across runs/reruns
- failed external check runs exposed through GitHub's checks APIs
- failed commit statuses exposed through GitHub's status APIs

The tool deduplicates repeated failures across reruns/check surfaces so the prompt stays compact:

- later successful reruns suppress older failures for the same Actions job identity
- repeated failures with the same identity are deduped to the most informative instance
- Actions jobs are preferred over fallback workflow-run items when job details are available
- external check runs and commit statuses are kept distinct unless they are obvious duplicates by context/name

Current-run failures remain first-class: when the current reusable-workflow run contributed the most
useful failure instance, it is preferred and rendered first among equivalent failures.

External checks depend on what GitHub exposes for the PR head SHA. Some providers offer only a
name, summary, and URL; when logs/details are unavailable, `pr-agent-context` renders a compact
metadata-only item instead of failing the run.

## Patch Coverage Rules

- Only changed Python files are considered.
- Added or modified line numbers come from `git diff --unified=0 <base>...<head>` in the checked-out caller repo.
- The denominator is changed executable Python lines only.
- Changed non-executable lines do not count against coverage.
- If a changed Python file is in coverage scope but has no measured data, its changed executable lines are treated as uncovered.
- If there are no changed executable Python lines, patch coverage is treated as `N/A`.
- The `# Codecov/patch` section is omitted for `N/A` patches unless `force_patch_coverage_section` is enabled.
- Uncovered lines are rendered as explicit line lists rather than compressed ranges so the output is easy to hand to a coding agent.

## Copilot Classification

By default, `pr-agent-context` treats these review actors as Copilot comments:

- exact matches for `copilot-pull-request-reviewer[bot]` and `github-copilot[bot]`
- regex matches from the built-in `re:copilot.*bot`

You can override or extend this with `copilot_author_patterns`. Exact logins are matched
literally; regex entries must start with `re:`.

Example:

```yaml
with:
  copilot_author_patterns: |
    copilot-pull-request-reviewer[bot]
    github-copilot[bot]
    re:my-org-copilot-.*
```

## Truncation Behavior

Large PRs are budgeted deterministically by section so the prompt remains useful instead of
failing hard on size:

- failing job excerpts are reduced first
- broader failing-check excerpts are reduced first
- long review-thread reply bodies are reduced next
- less-important metadata is reduced after that
- uncovered patch line lists are preserved unless the section hits a hard last-resort limit

When truncation happens, the affected item includes a visible note and the debug `summary.json`
records structured truncation metadata.

## Reusable Workflow Outputs

The reusable workflow exposes these outputs for downstream observability and automation:

- `comment_written`
- `comment_id`
- `comment_url`
- `prompt_sha256`
- `unresolved_thread_count`
- `failing_check_count`
- `patch_coverage_percent`
- `has_actionable_items`

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

## API / Permission Caveats

- GitHub Actions run/job aggregation requires `actions: read`.
- External check runs and commit statuses rely on GitHub's checks/status APIs for the PR head SHA.
- When some failing-check APIs are unavailable or partially denied, the tool degrades gracefully:
  it keeps the run alive, records warnings in `failing-check-universe.json`, and renders whatever
  useful failing-check context could still be collected.
- If `pr-agent-context` itself hits an unexpected internal error, it now degrades gracefully:
  it logs the traceback, attempts to upsert a managed alert comment with a concise failure summary,
  and exits successfully so the surrounding CI workflow is not blocked by the reporting step itself.

If a repo uses unusual layout or path rewriting, `pr-agent-context` will still merge the
available `.coverage*` files locally, but patch coverage quality depends on the caller
repo's `coverage.py` path normalization being sane.
