from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from agentbraid.errors import InvalidTransitionError, RunNotFoundError, StateError
from agentbraid.models import (
    CapabilitySnapshot,
    Executor,
    HostTaskResult,
    LocalizedRunNames,
    ProviderInvocationOutcome,
    ProviderUsageRecord,
    RoutingDecision,
    RunExecutionSettings,
    RunPlan,
    RunStatus,
    StartRunRequest,
    TaskKind,
    TaskOutcome,
    TaskSpec,
    TaskStatus,
    WorkerResult,
    WorkspaceSettings,
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


def test_delivery_target_and_provider_usage_survive_restart(tmp_path: Path) -> None:
    database_path = tmp_path / "state" / "agentbraid.db"
    store = StateStore(database_path)
    run = store.create_run(
        StartRunRequest(goal="Persist metadata."),
        base_branch="main",
        base_commit="a" * 40,
    )
    store.record_provider_usage(
        run.run_id,
        ProviderUsageRecord(
            phase="planning",
            executor=Executor.CODEX,
            model="gpt-test",
            input_tokens=100,
            cached_input_tokens=40,
            output_tokens=20,
            reasoning_output_tokens=5,
            duration_seconds=1.5,
        ),
    )

    snapshot = StateStore(database_path).get_run(run.run_id)

    assert snapshot.base_branch == "main"
    assert snapshot.base_commit == "a" * 40
    assert len(snapshot.provider_usage) == 1
    assert snapshot.provider_usage[0].cached_input_tokens == 40


def test_schema_v1_database_migrates_to_v4(tmp_path: Path) -> None:
    database_path = tmp_path / "agentbraid.db"
    connection = sqlite3.connect(database_path)
    connection.executescript(
        """
        CREATE TABLE runs (
            run_id TEXT PRIMARY KEY,
            request_json TEXT NOT NULL,
            status TEXT NOT NULL,
            plan_json TEXT,
            lead_thread_id TEXT,
            integration_branch TEXT,
            final_summary TEXT,
            error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        PRAGMA user_version = 1;
        """
    )
    connection.close()

    StateStore(database_path)

    migrated = sqlite3.connect(database_path)
    assert migrated.execute("PRAGMA user_version").fetchone()[0] == 4
    run_columns = {row[1] for row in migrated.execute("PRAGMA table_info(runs)")}
    assert {
        "base_branch",
        "base_commit",
        "workspace",
        "display_names_json",
        "execution_settings_json",
    } <= run_columns
    usage_table = migrated.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'provider_usage'"
    ).fetchone()
    assert usage_table is not None
    usage_columns = {row[1] for row in migrated.execute("PRAGMA table_info(provider_usage)")}
    assert {"attempt", "outcome"} <= usage_columns
    settings_table = migrated.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'workspace_settings'"
    ).fetchone()
    assert settings_table is not None
    migrated.close()


def test_schema_v2_database_migrates_workspace_and_usage_attribution(tmp_path: Path) -> None:
    database_path = tmp_path / "agentbraid.db"
    request = StartRunRequest(goal="Migrate me.", workspace="/tmp/example-workspace")
    connection = sqlite3.connect(database_path)
    connection.executescript(
        """
        CREATE TABLE runs (
            run_id TEXT PRIMARY KEY,
            request_json TEXT NOT NULL,
            status TEXT NOT NULL,
            plan_json TEXT,
            lead_thread_id TEXT,
            integration_branch TEXT,
            base_branch TEXT,
            base_commit TEXT,
            final_summary TEXT,
            error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE provider_usage (
            usage_id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            task_id TEXT,
            phase TEXT NOT NULL,
            executor TEXT NOT NULL,
            model TEXT NOT NULL,
            input_tokens INTEGER NOT NULL,
            cached_input_tokens INTEGER NOT NULL,
            output_tokens INTEGER NOT NULL,
            reasoning_output_tokens INTEGER NOT NULL,
            duration_seconds REAL NOT NULL,
            created_at TEXT NOT NULL
        );
        PRAGMA user_version = 2;
        """
    )
    connection.execute(
        """
        INSERT INTO runs (
            run_id, request_json, status, created_at, updated_at
        ) VALUES ('legacy-run', ?, 'completed', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')
        """,
        (request.model_dump_json(),),
    )
    connection.execute(
        """
        INSERT INTO provider_usage (
            run_id, phase, executor, model, input_tokens, cached_input_tokens,
            output_tokens, reasoning_output_tokens, duration_seconds, created_at
        ) VALUES (
            'legacy-run', 'planning', 'codex', 'gpt-test', 100, 40, 20, 5, 1.0,
            '2026-01-01T00:00:00Z'
        )
        """
    )
    connection.commit()
    connection.close()

    StateStore(database_path)

    migrated = sqlite3.connect(database_path)
    assert migrated.execute("PRAGMA user_version").fetchone()[0] == 4
    assert (
        migrated.execute("SELECT workspace FROM runs WHERE run_id = 'legacy-run'").fetchone()[0]
        == "/tmp/example-workspace"
    )
    assert migrated.execute(
        "SELECT attempt, outcome FROM provider_usage WHERE run_id = 'legacy-run'"
    ).fetchone() == (None, None)
    migrated.close()


def test_schema_v3_database_migrates_names_settings_and_workspace_defaults(
    tmp_path: Path,
) -> None:
    database_path = tmp_path / "agentbraid.db"
    connection = sqlite3.connect(database_path)
    connection.executescript(
        """
        CREATE TABLE runs (
            run_id TEXT PRIMARY KEY,
            request_json TEXT NOT NULL,
            workspace TEXT NOT NULL,
            status TEXT NOT NULL,
            plan_json TEXT,
            lead_thread_id TEXT,
            integration_branch TEXT,
            base_branch TEXT,
            base_commit TEXT,
            final_summary TEXT,
            error TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        PRAGMA user_version = 3;
        """
    )
    connection.close()

    StateStore(database_path)

    migrated = sqlite3.connect(database_path)
    assert migrated.execute("PRAGMA user_version").fetchone()[0] == 4
    run_columns = {row[1] for row in migrated.execute("PRAGMA table_info(runs)")}
    assert {"display_names_json", "execution_settings_json"} <= run_columns
    assert migrated.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'workspace_settings'"
    ).fetchone() == ("workspace_settings",)
    migrated.close()


def test_run_names_execution_snapshot_and_workspace_settings_persist(tmp_path: Path) -> None:
    database_path = tmp_path / "agentbraid.db"
    store = StateStore(database_path)
    names = LocalizedRunNames.model_validate(
        {
            "en": "Inspect project README",
            "zh-TW": "檢查專案 README",
            "zh-CN": "检查项目 README",
        }
    )
    execution = RunExecutionSettings(
        codex_binary="codex",
        codex_model="gpt-test",
        host_model="agy-label",
        max_parallel_codex=2,
        worktree_dir=str(tmp_path / "worktrees"),
    )
    run = store.create_run(
        StartRunRequest(goal="Inspect README.", workspace=str(tmp_path)),
        display_names=names,
        execution_settings=execution,
    )
    workspace_settings = WorkspaceSettings(
        **execution.model_dump(),
        workspace=str(tmp_path),
    )
    store.upsert_workspace_settings(workspace_settings)

    restarted = StateStore(database_path)
    snapshot = restarted.get_run(run.run_id)
    summary = restarted.list_runs()[0]
    saved_settings = restarted.get_workspace_settings(str(tmp_path))

    assert snapshot.display_names == names
    assert snapshot.execution_settings == execution
    assert summary.display_names == names
    assert saved_settings is not None
    assert saved_settings.codex_model == "gpt-test"
    assert saved_settings.workspace == str(tmp_path.resolve())

    updated = names.model_copy(update={"zh_tw": "檢視專案 README"})
    restarted.update_run_names(run.run_id, updated)
    assert restarted.get_run(run.run_id).display_names == updated
    assert restarted.list_run_events(run.run_id)[-1].event_type == "run.renamed"


def test_delete_run_record_cascades_local_database_state(tmp_path: Path) -> None:
    database_path = tmp_path / "agentbraid.db"
    store = StateStore(database_path)
    task, decision = make_task("delete-me", executor=Executor.CODEX)
    run_id = create_planned_run(store, (task, decision))
    store.record_provider_usage(
        run_id,
        ProviderUsageRecord(
            phase="task",
            executor=Executor.CODEX,
            model="gpt-test",
            task_id="delete-me",
            attempt=1,
            outcome=ProviderInvocationOutcome.FAILED,
        ),
    )
    store.cancel_run(run_id)

    store.delete_run_record(run_id)

    with pytest.raises(RunNotFoundError):
        store.get_run(run_id)
    connection = sqlite3.connect(database_path)
    for table in ("tasks", "task_dependencies", "events", "provider_usage"):
        assert (
            connection.execute(
                f"SELECT COUNT(*) FROM {table} WHERE run_id = ?", (run_id,)
            ).fetchone()[0]
            == 0
        )
    connection.close()


def test_run_and_workspace_summaries_include_retry_usage(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "agentbraid.db")
    first = store.create_run(
        StartRunRequest(goal="First run.", workspace="/tmp/project-one"),
        run_id="first-run",
        base_branch="main",
    )
    store.create_run(
        StartRunRequest(goal="Second run.", workspace="/tmp/project-two"),
        run_id="second-run",
    )
    for attempt, outcome, input_tokens in (
        (1, ProviderInvocationOutcome.FAILED, 100),
        (2, ProviderInvocationOutcome.SUCCEEDED, 60),
    ):
        store.record_provider_usage(
            first.run_id,
            ProviderUsageRecord(
                phase="task",
                executor=Executor.CODEX,
                model="gpt-test",
                task_id="retry-task",
                attempt=attempt,
                outcome=outcome,
                input_tokens=input_tokens,
                output_tokens=20,
            ),
        )

    all_runs = store.list_runs()
    project_runs = store.list_runs(workspace="/tmp/project-one")
    workspaces = store.list_workspaces()

    assert {run.run_id for run in all_runs} == {"first-run", "second-run"}
    assert [run.run_id for run in project_runs] == ["first-run"]
    assert project_runs[0].observed_total_tokens == 200
    assert project_runs[0].retry_tokens == 120
    assert [(item.workspace, item.run_count) for item in workspaces] == [
        ("/tmp/project-two", 1),
        ("/tmp/project-one", 1),
    ]


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


def test_persisted_prompts_results_and_events_are_redacted(tmp_path: Path) -> None:
    store = StateStore(tmp_path / "agentbraid.db")
    created = store.create_run(StartRunRequest(goal="Inspect with token=top-secret-value"))
    store.begin_planning(created.run_id)
    task, route = make_task("redact")
    redacted_task = task.model_copy(
        update={"instructions": "Do not log password=task-secret-value"}
    )
    store.save_plan(
        created.run_id,
        RunPlan(
            summary="Redact sensitive values.",
            tasks=[redacted_task],
            final_acceptance_criteria=["No secret is persisted."],
        ),
        {"redact": route},
    )
    assert store.claim_host_task(created.run_id, "token=host-secret-value") is not None
    store.submit_task_result(
        created.run_id,
        "redact",
        WorkerResult(
            outcome=TaskOutcome.SUCCEEDED,
            summary="Completed with authorization=result-secret-value",
        ),
        claimed_by="token=host-secret-value",
    )

    snapshot_json = store.get_run(created.run_id).model_dump_json()
    events_json = str(store.list_events(created.run_id))

    for secret in (
        "top-secret-value",
        "task-secret-value",
        "host-secret-value",
        "result-secret-value",
    ):
        assert secret not in snapshot_json
        assert secret not in events_json
    assert "[REDACTED]" in snapshot_json
