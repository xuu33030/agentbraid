from __future__ import annotations

import pytest
from pydantic import ValidationError

from agentbraid.models import (
    ReviewFinding,
    ReviewSeverity,
    RunPlan,
    RunReview,
    TaskKind,
    TaskSpec,
)


def task(task_id: str, *, dependencies: list[str] | None = None) -> TaskSpec:
    return TaskSpec(
        task_id=task_id,
        title=f"Task {task_id}",
        instructions="Complete the assigned task.",
        kind=TaskKind.IMPLEMENTATION,
        dependencies=dependencies or [],
        acceptance_criteria=["The task is complete."],
    )


def plan(*tasks: TaskSpec) -> RunPlan:
    return RunPlan(
        summary="A validated execution plan.",
        tasks=list(tasks),
        final_acceptance_criteria=["Every task succeeds."],
    )


def test_run_plan_returns_stable_topological_order() -> None:
    run_plan = plan(
        task("document", dependencies=["build"]),
        task("review", dependencies=["build"]),
        task("build", dependencies=["inspect"]),
        task("inspect"),
    )

    assert run_plan.topological_ids() == ["inspect", "build", "document", "review"]


def test_run_plan_rejects_unknown_dependency() -> None:
    with pytest.raises(ValidationError, match="unknown dependencies: missing"):
        plan(task("build", dependencies=["missing"]))


def test_run_plan_rejects_cycle() -> None:
    with pytest.raises(ValidationError, match="contains a cycle"):
        plan(
            task("first", dependencies=["second"]),
            task("second", dependencies=["first"]),
        )


def test_run_plan_rejects_duplicate_task_ids() -> None:
    with pytest.raises(ValidationError, match="task IDs must be unique"):
        plan(task("build"), task("build"))


def test_approved_review_rejects_error_findings() -> None:
    with pytest.raises(ValidationError, match="cannot contain error findings"):
        RunReview(
            approved=True,
            summary="This review is internally inconsistent.",
            findings=[
                ReviewFinding(
                    severity=ReviewSeverity.ERROR,
                    title="Regression",
                    detail="A blocking regression remains.",
                )
            ],
        )
