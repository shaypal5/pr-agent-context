from __future__ import annotations

from pr_agent_context.domain.models import ReviewThread, WorkflowFailure


def assign_item_ids(
    review_threads: list[ReviewThread],
    workflow_failures: list[WorkflowFailure],
) -> tuple[list[ReviewThread], list[WorkflowFailure]]:
    copilot_threads = sorted(
        [thread for thread in review_threads if thread.classifier == "copilot"],
        key=lambda thread: thread.thread_id,
    )
    review_only_threads = sorted(
        [thread for thread in review_threads if thread.classifier != "copilot"],
        key=lambda thread: thread.thread_id,
    )
    failures = sorted(
        workflow_failures,
        key=lambda failure: (
            failure.workflow_name,
            failure.job_name,
            failure.matrix_label or "",
            failure.job_id,
        ),
    )

    numbered_copilot = [
        thread.model_copy(update={"item_id": f"COPILOT-{index}"})
        for index, thread in enumerate(copilot_threads, start=1)
    ]
    numbered_review = [
        thread.model_copy(update={"item_id": f"REVIEW-{index}"})
        for index, thread in enumerate(review_only_threads, start=1)
    ]
    numbered_failures = [
        failure.model_copy(update={"item_id": f"FAIL-{index}"})
        for index, failure in enumerate(failures, start=1)
    ]
    return numbered_copilot + numbered_review, numbered_failures
