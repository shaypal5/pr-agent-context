from __future__ import annotations

from pathlib import Path

import yaml


REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_workflow(relative_path: str) -> dict[str, object]:
    workflow = yaml.load(
        (REPO_ROOT / relative_path).read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )
    assert isinstance(workflow, dict)
    return workflow


def test_self_refresh_uses_local_reusable_workflow_contract():
    refresh_workflow = _load_workflow(".github/workflows/pr-agent-context-refresh.yml")
    reusable_workflow = _load_workflow(".github/workflows/pr-agent-context.yml")

    refresh_job = refresh_workflow["jobs"]["pr-agent-context-refresh"]
    reusable_inputs = reusable_workflow["on"]["workflow_call"]["inputs"]

    assert refresh_job["uses"] == "./.github/workflows/pr-agent-context.yml"
    assert refresh_job["with"]["tool_ref"] == "${{ github.event.pull_request.head.sha || github.sha }}"
    assert set(refresh_job["with"]).issubset(set(reusable_inputs))
