# Managed Comment Preview

This abbreviated example shows the shape of the PR comment that
`pr-agent-context` publishes for coding agents. Real comments include the same
managed marker and run metadata, with sections populated from the current PR.

````markdown
<!-- pr-agent-context:managed-comment; schema=v5; publish_mode=append; execution_mode=ci; pr=42; head_sha=abc123; trigger_event=pull_request; generated_at=2026-05-30T12:34:56Z; tool_ref=v4; run_id=123456789; run_attempt=1 -->
pr-agent-context report:
```markdown
# PR 42

This run includes unresolved review comments, failing checks, and patch
coverage findings for the current PR head.

## Unresolved Review Comments

### COPILOT-1
Location: src/example.py:37
URL: https://github.com/example/repo/pull/42#discussion_r123

Comment:
    This branch accepts an empty payload and later crashes. Add validation
    before constructing the request object.

## Failing Checks

### test (ubuntu-latest, py3.12)
Conclusion: failure
Details: https://github.com/example/repo/actions/runs/123456789/job/987654321

Failed step output:
    AssertionError: expected retry_count == 3

## Patch Coverage

Target: 100%
Actual: 91.67%

Uncovered changed executable Python lines:
- src/example.py: 37
```
Run metadata:
```
Tool ref: v4
Tool version: 4.0.21
Workflow run: 123456789 attempt 1
Comment timestamp: 2026-05-30T12:34:56Z
PR head commit: abc123
```
````
