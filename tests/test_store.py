from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from agentbraid.errors import InvalidTransitionError, StateError
from agentbraid.models import (
    CapabilitySnapshot,
    Executor,
    HostTaskResult,
    RoutingDecision,
    RunPlan,
    RunStatus,
    StartRunRequest,
    TaskKind,
    TaskOutcome,
    TaskSpec,
    TaskStatus,
    WorkerResult,
)
from agentbraid.store import StateStore


def make_task(
    task_id: str,
    *,
    dependencies: list[str] | None = None,
    executor: Executor = Executor.HOST,
    mutates_workspace: bool = False,
    max_attempts: int = 2,
) -> tuple[TaskSpec, RoutingDecision]:
    spec = TaskSpec(
        task_id=task_id,
        title=f"Task {task_id}",
        instructions="Complete the task and return structured evidence.",
        kind=TaskKind.IMPLEMENTATION,
        preferred_executor=executor,
        mutates_workspace=mutates_workspace,
        dependencies=dependencies or [],
        acceptance_criteria=["The requested outcome is verified."],
        max_attempts=max_attempts,
    )
    decision = RoutingDecision(
        executor=executor,
        score=1.0,
        rationale=f"Assigned to {executor.value} for the test.",
    )
    return spec, decision


def create_planned_run(
    store: StateStore,
    *task_pairs: tuple[TaskSpec, RoutingDecision],
) -> str:
    request = StartRunRequest(goal="Build and validate the requested change.")
    snapshot = store.create_run(request)
    store.begin_planning(snapshot.run_id)
    run_plan = RunPlan(
        summary="Test execution plan.",
        tasks=[pair[0] for pair in task_pairs],
        final_acceptance_criteria=["All tasks pass."],
    )
    store.save_plan(
        snapshot.run_id,
        run_plan,
        {pair[0].task_id: pair[1] for pair in task_pairs},
        lead_thread_id="thread-test",
        integration_branch=f"agentbraid/{snapshot.run_id}",
    )
    return snapshot.run_id


def succeeded(summary: str = "Task completed.") -> WorkerResult:
    return WorkerResult(outcome=TaskOutcome.SUCCEEDED, summary=summary, confidence=0.9)


def test_run_and_plan_survive_store_restart(tmp_path: Path) -> None:
    database_path = tmp_path / "state" / "agentbraid.db"
    first, first_route = make_task("inspect")
    second, second_route = make_task("build", dependencies=["inspect"])
    run_id = create_planned_run(
        StateStore(database_path),
        (second, second_route),
        (first, first_route),
    )

    snapshot = StateStore(database_path).get_run(run_id)

    assert snapshot.status == RunStatus.RUNNING
    assert snapshot.lead_thread_id == "thread-test"
    assert snapshot.plan is not None
    assert [task.spec.task_id for task in snapshot.tasks] == ["build", "inspect"]
    assert [task.status for task in snapshot.tasks] == [TaskStatus.PENDING, TaskStatus.READY]


def test_dependency_becomes_ready_after_parent_succeeds(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "agentbraid.db")
    inspect, inspect_route = make_task("inspect")
    build, build_route = make_task("build", dependencies=["inspect"])
    run_id = create_planned_run(store, (inspect, inspect_route), (build, build_route))

    claimed = store.claim_host_task(run_id, "antigravity-session")
    assert claimed is not None
    assert claimed.spec.task_id == "inspect"
    assert claimed.status == TaskStatus.RUNNING
    assert claimed.attempt == 1

    completed = store.submit_task_result(
        run_id,
        "inspect",
        HostTaskResult(outcome=TaskOutcome.SUCCEEDED, summary="Inspection complete."),
        claimed_by="antigravity-session",
    )
    snapshot = store.get_run(run_id)

    assert completed.status == TaskStatus.SUCCEEDED
    assert snapshot.tasks[1].status == TaskStatus.READY


def test_claim_is_atomic_across_connections(tmp_path: Path) -> None:
    database_path = tmp_path / "agentbraid.db"
    store = StateStore(database_path)
    task, route = make_task("single")
    run_id = create_planned_run(store, (task, route))

    def claim(worker: str) -> str | None:
        claimed = StateStore(database_path).claim_host_task(run_id, worker)
        return claimed.claimed_by if claimed is not None else None

    with ThreadPoolExecutor(max_workers=2) as pool:
        claims = list(pool.map(claim, ["host-one", "host-two"]))

    assert sum(claimed_by is not None for claimed_by in claims) == 1


def test_failed_task_retries_then_fails_run(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "agentbraid.db")
    task, route = make_task("flaky", max_attempts=2)
    run_id = create_planned_run(store, (task, route))
    failure = WorkerResult(
        outcome=TaskOutcome.FAILED,
        summary="Validation failed.",
        error="test command exited with 1",
    )

    first_claim = store.claim_host_task(run_id, "host")
    assert first_claim is not None
    first_result = store.submit_task_result(run_id, "flaky", failure, claimed_by="host")
    assert first_result.status == TaskStatus.READY

    second_claim = store.claim_host_task(run_id, "host")
    assert second_claim is not None
    second_result = store.submit_task_result(run_id, "flaky", failure, claimed_by="host")

    assert second_result.status == TaskStatus.FAILED
    assert second_result.attempt == 2
    assert store.get_run(run_id).status == RunStatus.FAILED


def test_failure_blocks_out_of_order_dependency_chain(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "agentbraid.db")
    final, final_route = make_task("final", dependencies=["middle"])
    middle, middle_route = make_task("middle", dependencies=["root"])
    root, root_route = make_task("root", max_attempts=1)
    run_id = create_planned_run(
        store,
        (final, final_route),
        (middle, middle_route),
        (root, root_route),
    )
    assert store.claim_host_task(run_id, "host") is not None

    store.submit_task_result(
        run_id,
        "root",
        WorkerResult(outcome=TaskOutcome.FAILED, summary="Root task failed."),
        claimed_by="host",
    )
    snapshot = store.get_run(run_id)

    assert [task.status for task in snapshot.tasks] == [
        TaskStatus.BLOCKED,
        TaskStatus.BLOCKED,
        TaskStatus.FAILED,
    ]
    assert snapshot.status == RunStatus.FAILED


def test_mutating_success_requires_commit_sha(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "agentbraid.db")
    task, route = make_task("edit", mutates_workspace=True)
    run_id = create_planned_run(store, (task, route))
    assert store.claim_host_task(run_id, "host") is not None

    with pytest.raises(StateError, match="must provide a commit SHA"):
        store.submit_task_result(run_id, "edit", succeeded(), claimed_by="host")

    result = HostTaskResult(
        outcome=TaskOutcome.SUCCEEDED,
        summary="Change committed.",
        commit_sha="a" * 40,
    )
    completed = store.submit_task_result(run_id, "edit", result, claimed_by="host")

    assert completed.status == TaskStatus.SUCCEEDED
    assert completed.commit_sha == "a" * 40
    assert store.get_run(run_id).status == RunStatus.INTEGRATING


def test_cancel_run_cancels_active_tasks(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "agentbraid.db")
    task, route = make_task("active")
    run_id = create_planned_run(store, (task, route))
    assert store.claim_host_task(run_id, "host") is not None

    cancelled = store.cancel_run(run_id)

    assert cancelled.status == RunStatus.CANCELLED
    assert cancelled.tasks[0].status == TaskStatus.CANCELLED
    with pytest.raises(InvalidTransitionError):
        store.set_run_status(run_id, RunStatus.RUNNING)


def test_capability_statistics_are_persisted(tmp_path: Path) -> None:
    database_path = tmp_path / "agentbraid.db"
    store = StateStore(database_path)
    store.upsert_capability(CapabilitySnapshot(executor=Executor.CODEX, model="gpt-test"))

    store.record_capability_result(
        Executor.CODEX,
        "gpt-test",
        succeeded=True,
        latency_seconds=2.0,
    )
    capability = StateStore(database_path).record_capability_result(
        Executor.CODEX,
        "gpt-test",
        succeeded=False,
        latency_seconds=4.0,
    )

    assert capability.successes == 1
    assert capability.failures == 1
    assert capability.average_latency_seconds == pytest.approx(3.0)


def test_events_record_run_and_task_transitions(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "agentbraid.db")
    task, route = make_task("observe")
    run_id = create_planned_run(store, (task, route))
    assert store.claim_host_task(run_id, "host") is not None
    store.submit_task_result(run_id, "observe", succeeded(), claimed_by="host")

    events = store.list_events(run_id)

    assert [event["event_type"] for event in events] == [
        "run.created",
        "run.status_changed",
        "task.status_changed",
        "run.planned",
        "task.claimed",
        "task.result_submitted",
        "run.status_changed",
    ]
