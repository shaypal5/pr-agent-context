# pr-agent-context

`pr-agent-context` is a reusable GitHub Actions tool that assembles PR review threads
and same-run failing job context into a single managed PR comment for coding-agent
handoffs.

Milestone 1 includes:

- unresolved PR review threads
- same-run failed GitHub Actions jobs with trimmed log excerpts
- deterministic prompt rendering with stable item IDs
- single managed PR comment upsert/delete using a hidden HTML marker

Example downstream usage:

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
```

The managed comment body shape is:

````markdown
<!-- pr-agent-context:managed-comment -->
```markdown
<rendered prompt>
```
````
