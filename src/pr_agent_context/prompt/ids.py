from __future__ import annotations

from pr_agent_context.domain.models import (
    FailingCheck,
    ReviewThread,
    failing_check_sort_key,
    review_thread_sort_key,
)


def assign_item_ids(
    review_threads: list[ReviewThread],
    failing_checks: list[FailingCheck],
) -> tuple[list[ReviewThread], list[FailingCheck]]:
    copilot_threads = sorted(
        [thread for thread in review_threads if thread.classifier == "copilot"],
        key=review_thread_sort_key,
    )
    review_only_threads = sorted(
        [thread for thread in review_threads if thread.classifier != "copilot"],
        key=review_thread_sort_key,
    )
    failures = sorted(
        failing_checks,
        key=failing_check_sort_key,
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
