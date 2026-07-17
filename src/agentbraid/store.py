from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

from agentbraid.errors import (
    InvalidTransitionError,
    RunNotFoundError,
    StateError,
    TaskNotFoundError,
)
from agentbraid.models import (
    CapabilitySnapshot,
    CapabilityStatus,
    DeliveryMode,
    Executor,
    HostTaskResult,
    LocalizedRunNames,
    ProviderInvocationOutcome,
    ProviderUsageRecord,
    RoutingDecision,
    RunEvent,
    RunExecutionSettings,
    RunPlan,
    RunSnapshot,
    RunStatus,
    RunSummary,
    StartRunRequest,
    TaskOutcome,
    TaskSpec,
    TaskState,
    TaskStatus,
    WorkerResult,
    WorkspaceSettings,
    WorkspaceSummary,
    utc_now,
)
from agentbraid.redaction import redact_model, redact_text, redact_value

SCHEMA_VERSION = 4

_RUN_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.CREATED: frozenset({RunStatus.PLANNING, RunStatus.CANCELLED, RunStatus.FAILED}),
    RunStatus.PLANNING: frozenset(
        {RunStatus.RUNNING, RunStatus.BLOCKED, RunStatus.CANCELLED, RunStatus.FAILED}
    ),
    RunStatus.RUNNING: frozenset(
        {
            RunStatus.INTEGRATING,
            RunStatus.REVIEWING,
            RunStatus.BLOCKED,
            RunStatus.CANCELLED,
            RunStatus.FAILED,
        }
    ),
    RunStatus.INTEGRATING: frozenset(
        {RunStatus.REVIEWING, RunStatus.BLOCKED, RunStatus.CANCELLED, RunStatus.FAILED}
    ),
    RunStatus.REVIEWING: frozenset(
        {RunStatus.COMPLETED, RunStatus.BLOCKED, RunStatus.CANCELLED, RunStatus.FAILED}
    ),
    RunStatus.BLOCKED: frozenset({RunStatus.RUNNING, RunStatus.CANCELLED, RunStatus.FAILED}),
    RunStatus.COMPLETED: frozenset(),
    RunStatus.CANCELLED: frozenset(),
    RunStatus.FAILED: frozenset(),
}

_TERMINAL_RUN_STATUSES = frozenset({RunStatus.COMPLETED, RunStatus.CANCELLED, RunStatus.FAILED})
_ACTIVE_TASK_STATUSES = frozenset(
    {
        TaskStatus.PENDING,
        TaskStatus.READY,
        TaskStatus.RUNNING,
        TaskStatus.RETRYING,
    }
)
_FAILED_DEPENDENCY_STATUSES = frozenset(
    {TaskStatus.FAILED, TaskStatus.BLOCKED, TaskStatus.CANCELLED}
)


class StateStore:
    """Durable SQLite state for runs, tasks, events, and capabilities."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path.expanduser().resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            if version > SCHEMA_VERSION:
                raise StateError(
                    "state database was created by a newer AgentBraid version",
                    detail=f"database={version}, supported={SCHEMA_VERSION}",
                )
            if version == 0:
                connection.executescript(
                    """
                    CREATE TABLE runs (
                        run_id TEXT PRIMARY KEY,
                        request_json TEXT NOT NULL,
                        workspace TEXT NOT NULL,
                        display_names_json TEXT,
                        execution_settings_json TEXT,
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

                    CREATE TABLE tasks (
                        run_id TEXT NOT NULL,
                        task_id TEXT NOT NULL,
                        position INTEGER NOT NULL,
                        spec_json TEXT NOT NULL,
                        status TEXT NOT NULL,
                        executor TEXT NOT NULL,
                        assignment_rationale TEXT NOT NULL,
                        attempt INTEGER NOT NULL DEFAULT 0,
                        claimed_by TEXT,
                        result_json TEXT,
                        result_kind TEXT,
                        worktree_path TEXT,
                        commit_sha TEXT,
                        error TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (run_id, task_id),
                        FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
                    );

                    CREATE TABLE task_dependencies (
                        run_id TEXT NOT NULL,
                        task_id TEXT NOT NULL,
                        depends_on_task_id TEXT NOT NULL,
                        PRIMARY KEY (run_id, task_id, depends_on_task_id),
                        FOREIGN KEY (run_id, task_id)
                            REFERENCES tasks(run_id, task_id) ON DELETE CASCADE,
                        FOREIGN KEY (run_id, depends_on_task_id)
                            REFERENCES tasks(run_id, task_id) ON DELETE CASCADE
                    );

                    CREATE TABLE events (
                        event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        run_id TEXT NOT NULL,
                        task_id TEXT,
                        event_type TEXT NOT NULL,
                        payload_json TEXT NOT NULL,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
                    );

                    CREATE TABLE capabilities (
                        executor TEXT NOT NULL,
                        model TEXT NOT NULL,
                        status TEXT NOT NULL,
                        successes INTEGER NOT NULL DEFAULT 0,
                        failures INTEGER NOT NULL DEFAULT 0,
                        total_latency_seconds REAL NOT NULL DEFAULT 0,
                        cooldown_until TEXT,
                        metadata_json TEXT NOT NULL,
                        updated_at TEXT NOT NULL,
                        PRIMARY KEY (executor, model)
                    );

                    CREATE TABLE provider_usage (
                        usage_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        run_id TEXT NOT NULL,
                        task_id TEXT,
                        phase TEXT NOT NULL,
                        executor TEXT NOT NULL,
                        model TEXT NOT NULL,
                        attempt INTEGER,
                        outcome TEXT,
                        input_tokens INTEGER NOT NULL,
                        cached_input_tokens INTEGER NOT NULL,
                        output_tokens INTEGER NOT NULL,
                        reasoning_output_tokens INTEGER NOT NULL,
                        duration_seconds REAL NOT NULL,
                        created_at TEXT NOT NULL,
                        FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
                    );

                    CREATE TABLE workspace_settings (
                        workspace TEXT PRIMARY KEY,
                        settings_json TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );

                    CREATE INDEX tasks_claim_idx
                        ON tasks(run_id, executor, status, position);
                    CREATE INDEX task_dependencies_reverse_idx
                        ON task_dependencies(run_id, depends_on_task_id);
                    CREATE INDEX events_run_idx
                        ON events(run_id, event_id);
                    CREATE INDEX provider_usage_run_idx
                        ON provider_usage(run_id, usage_id);
                    CREATE INDEX runs_workspace_updated_idx
                        ON runs(workspace, updated_at DESC, run_id DESC);
                    """
                )
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
                return
            if version == 1:
                connection.executescript(
                    """
                    ALTER TABLE runs ADD COLUMN base_branch TEXT;
                    ALTER TABLE runs ADD COLUMN base_commit TEXT;
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
                        created_at TEXT NOT NULL,
                        FOREIGN KEY (run_id) REFERENCES runs(run_id) ON DELETE CASCADE
                    );
                    CREATE INDEX provider_usage_run_idx
                        ON provider_usage(run_id, usage_id);
                    """
                )
                connection.execute("PRAGMA user_version = 2")
                version = 2
            if version == 2:
                connection.executescript(
                    """
                    ALTER TABLE runs ADD COLUMN workspace TEXT NOT NULL DEFAULT '';
                    ALTER TABLE provider_usage ADD COLUMN attempt INTEGER;
                    ALTER TABLE provider_usage ADD COLUMN outcome TEXT;
                    CREATE INDEX runs_workspace_updated_idx
                        ON runs(workspace, updated_at DESC, run_id DESC);
                    """
                )
                rows = connection.execute("SELECT run_id, request_json FROM runs").fetchall()
                for row in rows:
                    request = StartRunRequest.model_validate_json(row["request_json"])
                    connection.execute(
                        "UPDATE runs SET workspace = ? WHERE run_id = ?",
                        (request.workspace or "", row["run_id"]),
                    )
                connection.execute("PRAGMA user_version = 3")
                version = 3
            if version == 3:
                connection.executescript(
                    """
                    ALTER TABLE runs ADD COLUMN display_names_json TEXT;
                    ALTER TABLE runs ADD COLUMN execution_settings_json TEXT;
                    CREATE TABLE workspace_settings (
                        workspace TEXT PRIMARY KEY,
                        settings_json TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    );
                    """
                )
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(
            self.database_path,
            timeout=5,
            isolation_level=None,
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _transaction(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            try:
                yield connection
            except BaseException:
                connection.rollback()
                raise
            else:
                connection.commit()

    def create_run(
        self,
        request: StartRunRequest,
        *,
        run_id: str | None = None,
        base_branch: str | None = None,
        base_commit: str | None = None,
        execution_settings: RunExecutionSettings | None = None,
        display_names: LocalizedRunNames | None = None,
    ) -> RunSnapshot:
        identifier = run_id or uuid4().hex
        request = redact_model(request)
        now = utc_now()
        with self._transaction() as connection:
            try:
                connection.execute(
                    """
                    INSERT INTO runs (
                        run_id, request_json, workspace, display_names_json,
                        execution_settings_json, status, base_branch, base_commit,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        identifier,
                        request.model_dump_json(),
                        request.workspace or "",
                        display_names.model_dump_json(by_alias=True)
                        if display_names is not None
                        else None,
                        execution_settings.model_dump_json()
                        if execution_settings is not None
                        else None,
                        RunStatus.CREATED.value,
                        base_branch,
                        base_commit,
                        _dump_datetime(now),
                        _dump_datetime(now),
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise StateError(f"run already exists: {identifier}") from exc
            self._append_event(
                connection,
                identifier,
                "run.created",
                {"status": RunStatus.CREATED.value},
                created_at=now,
            )
        return self.get_run(identifier)

    def get_run(self, run_id: str) -> RunSnapshot:
        with self._connect() as connection:
            run_row = self._get_run_row(connection, run_id)
            task_rows = connection.execute(
                "SELECT * FROM tasks WHERE run_id = ? ORDER BY position, task_id",
                (run_id,),
            ).fetchall()
            usage_rows = connection.execute(
                "SELECT * FROM provider_usage WHERE run_id = ? ORDER BY usage_id",
                (run_id,),
            ).fetchall()
        return self._run_snapshot(run_row, task_rows, usage_rows)

    def get_run_status(self, run_id: str) -> RunStatus:
        with self._connect() as connection:
            return RunStatus(self._get_run_row(connection, run_id)["status"])

    def list_runs(
        self,
        *,
        workspace: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[RunSummary]:
        if not 1 <= limit <= 100:
            raise StateError("run list limit must be between 1 and 100")
        if offset < 0:
            raise StateError("run list offset must not be negative")
        where_clause = "WHERE r.workspace = ?" if workspace is not None else ""
        parameters: list[object] = [workspace] if workspace is not None else []
        parameters.extend([limit, offset])
        with self._connect() as connection:
            rows = connection.execute(
                f"""
                WITH task_totals AS (
                    SELECT
                        run_id,
                        COUNT(*) AS task_count,
                        SUM(CASE WHEN status = 'succeeded' THEN 1 ELSE 0 END)
                            AS succeeded_task_count,
                        SUM(CASE WHEN status IN ('failed', 'blocked', 'cancelled')
                            THEN 1 ELSE 0 END) AS failed_task_count
                    FROM tasks
                    GROUP BY run_id
                ),
                usage_totals AS (
                    SELECT run_id, SUM(input_tokens + output_tokens) AS observed_total_tokens
                    FROM provider_usage
                    GROUP BY run_id
                ),
                max_attempts AS (
                    SELECT run_id, task_id, MAX(attempt) AS final_attempt
                    FROM provider_usage
                    WHERE phase = 'task' AND task_id IS NOT NULL AND attempt IS NOT NULL
                    GROUP BY run_id, task_id
                ),
                retry_totals AS (
                    SELECT p.run_id, SUM(p.input_tokens + p.output_tokens) AS retry_tokens
                    FROM provider_usage AS p
                    JOIN max_attempts AS m
                        ON m.run_id = p.run_id AND m.task_id = p.task_id
                    WHERE p.attempt < m.final_attempt
                    GROUP BY p.run_id
                )
                SELECT
                    r.*,
                    COALESCE(t.task_count, 0) AS task_count,
                    COALESCE(t.succeeded_task_count, 0) AS succeeded_task_count,
                    COALESCE(t.failed_task_count, 0) AS failed_task_count,
                    COALESCE(u.observed_total_tokens, 0) AS observed_total_tokens,
                    COALESCE(rt.retry_tokens, 0) AS retry_tokens
                FROM runs AS r
                LEFT JOIN task_totals AS t ON t.run_id = r.run_id
                LEFT JOIN usage_totals AS u ON u.run_id = r.run_id
                LEFT JOIN retry_totals AS rt ON rt.run_id = r.run_id
                {where_clause}
                ORDER BY r.updated_at DESC, r.run_id DESC
                LIMIT ? OFFSET ?
                """,
                parameters,
            ).fetchall()
        return [self._run_summary(row) for row in rows]

    def list_workspaces(self) -> list[WorkspaceSummary]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                WITH usage_totals AS (
                    SELECT run_id, SUM(input_tokens + output_tokens) AS observed_total_tokens
                    FROM provider_usage
                    GROUP BY run_id
                )
                SELECT
                    r.workspace,
                    COUNT(*) AS run_count,
                    SUM(CASE WHEN r.status NOT IN ('completed', 'cancelled', 'failed')
                        THEN 1 ELSE 0 END) AS active_run_count,
                    COALESCE(SUM(u.observed_total_tokens), 0) AS observed_total_tokens,
                    MAX(r.updated_at) AS updated_at
                FROM runs AS r
                LEFT JOIN usage_totals AS u ON u.run_id = r.run_id
                WHERE r.workspace != ''
                GROUP BY r.workspace
                ORDER BY updated_at DESC, r.workspace
                """
            ).fetchall()
            settings_rows = connection.execute(
                "SELECT workspace, updated_at FROM workspace_settings ORDER BY updated_at DESC"
            ).fetchall()
        summaries = [
            WorkspaceSummary(
                workspace=row["workspace"],
                run_count=int(row["run_count"]),
                active_run_count=int(row["active_run_count"]),
                observed_total_tokens=int(row["observed_total_tokens"]),
                updated_at=_load_datetime(row["updated_at"]),
            )
            for row in rows
        ]
        known = {item.workspace for item in summaries}
        summaries.extend(
            WorkspaceSummary(
                workspace=row["workspace"],
                run_count=0,
                active_run_count=0,
                observed_total_tokens=0,
                updated_at=_load_datetime(row["updated_at"]),
            )
            for row in settings_rows
            if row["workspace"] not in known
        )
        return sorted(summaries, key=lambda item: (item.updated_at, item.workspace), reverse=True)

    def get_workspace_settings(self, workspace: str) -> WorkspaceSettings | None:
        resolved = str(Path(workspace).expanduser().resolve())
        with self._connect() as connection:
            row = connection.execute(
                "SELECT settings_json FROM workspace_settings WHERE workspace = ?",
                (resolved,),
            ).fetchone()
        if row is None:
            return None
        return WorkspaceSettings.model_validate_json(row["settings_json"])

    def upsert_workspace_settings(self, settings: WorkspaceSettings) -> WorkspaceSettings:
        settings = redact_model(settings)
        resolved = str(Path(settings.workspace).expanduser().resolve())
        normalized = settings.model_copy(update={"workspace": resolved, "updated_at": utc_now()})
        with self._transaction(immediate=True) as connection:
            connection.execute(
                """
                INSERT INTO workspace_settings (workspace, settings_json, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(workspace) DO UPDATE SET
                    settings_json = excluded.settings_json,
                    updated_at = excluded.updated_at
                """,
                (
                    resolved,
                    normalized.model_dump_json(),
                    _dump_datetime(normalized.updated_at),
                ),
            )
        return normalized

    def list_observed_models(self, executor: Executor) -> list[str]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT model, MAX(updated_at) AS observed_at
                FROM (
                    SELECT model, updated_at FROM capabilities WHERE executor = ?
                    UNION ALL
                    SELECT model, created_at AS updated_at FROM provider_usage WHERE executor = ?
                )
                GROUP BY model
                ORDER BY observed_at DESC, model
                """,
                (executor.value, executor.value),
            ).fetchall()
        return [str(row["model"]) for row in rows]

    def delete_run_record(self, run_id: str) -> None:
        with self._transaction(immediate=True) as connection:
            row = self._get_run_row(connection, run_id)
            status = RunStatus(row["status"])
            if status not in _TERMINAL_RUN_STATUSES:
                raise StateError(f"run must be terminal before deletion: {status.value}")
            connection.execute("DELETE FROM runs WHERE run_id = ?", (run_id,))

    def begin_planning(self, run_id: str) -> RunSnapshot:
        return self.set_run_status(run_id, RunStatus.PLANNING)

    def save_plan(
        self,
        run_id: str,
        plan: RunPlan,
        assignments: Mapping[str, RoutingDecision],
        *,
        lead_thread_id: str | None = None,
        integration_branch: str | None = None,
    ) -> RunSnapshot:
        plan = redact_model(plan)
        task_ids = {task.task_id for task in plan.tasks}
        assignment_ids = set(assignments)
        if task_ids != assignment_ids:
            missing = sorted(task_ids - assignment_ids)
            extra = sorted(assignment_ids - task_ids)
            detail = f"missing={missing}, extra={extra}"
            raise StateError("routing assignments must exactly match the plan", detail=detail)

        now = utc_now()
        with self._transaction(immediate=True) as connection:
            run_row = self._get_run_row(connection, run_id)
            current = RunStatus(run_row["status"])
            if current not in {RunStatus.CREATED, RunStatus.PLANNING}:
                raise InvalidTransitionError(f"cannot save a plan while run is {current.value}")
            existing = connection.execute(
                "SELECT 1 FROM tasks WHERE run_id = ? LIMIT 1", (run_id,)
            ).fetchone()
            if existing is not None:
                raise StateError(f"run already has a persisted plan: {run_id}")

            connection.execute(
                """
                UPDATE runs
                SET plan_json = ?, display_names_json = COALESCE(?, display_names_json),
                    status = ?, lead_thread_id = ?,
                    integration_branch = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (
                    plan.model_dump_json(),
                    plan.display_names.model_dump_json(by_alias=True)
                    if plan.display_names is not None
                    else None,
                    RunStatus.RUNNING.value,
                    lead_thread_id,
                    integration_branch,
                    _dump_datetime(now),
                    run_id,
                ),
            )
            for position, task in enumerate(plan.tasks):
                decision = assignments[task.task_id]
                connection.execute(
                    """
                    INSERT INTO tasks (
                        run_id, task_id, position, spec_json, status, executor,
                        assignment_rationale, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        task.task_id,
                        position,
                        task.model_dump_json(),
                        TaskStatus.PENDING.value,
                        decision.executor.value,
                        redact_text(decision.rationale),
                        _dump_datetime(now),
                        _dump_datetime(now),
                    ),
                )
            for task in plan.tasks:
                connection.executemany(
                    """
                    INSERT INTO task_dependencies (run_id, task_id, depends_on_task_id)
                    VALUES (?, ?, ?)
                    """,
                    ((run_id, task.task_id, dependency) for dependency in task.dependencies),
                )
            self._refresh_ready_tasks(connection, run_id, now)
            self._append_event(
                connection,
                run_id,
                "run.planned",
                {
                    "status": RunStatus.RUNNING.value,
                    "task_count": len(plan.tasks),
                    "schema_version": plan.schema_version,
                },
                created_at=now,
            )
        return self.get_run(run_id)

    def update_run_names(
        self,
        run_id: str,
        display_names: LocalizedRunNames,
    ) -> RunSnapshot:
        display_names = redact_model(display_names)
        now = utc_now()
        with self._transaction(immediate=True) as connection:
            row = self._get_run_row(connection, run_id)
            previous = row["display_names_json"]
            connection.execute(
                "UPDATE runs SET display_names_json = ?, updated_at = ? WHERE run_id = ?",
                (
                    display_names.model_dump_json(by_alias=True),
                    _dump_datetime(now),
                    run_id,
                ),
            )
            self._append_event(
                connection,
                run_id,
                "run.renamed",
                {
                    "previous": json.loads(previous) if previous is not None else None,
                    "display_names": display_names.model_dump(mode="json", by_alias=True),
                },
                created_at=now,
            )
        return self.get_run(run_id)

    def set_run_status(
        self,
        run_id: str,
        status: RunStatus,
        *,
        final_summary: str | None = None,
        error: str | None = None,
    ) -> RunSnapshot:
        now = utc_now()
        with self._transaction(immediate=True) as connection:
            row = self._get_run_row(connection, run_id)
            current = RunStatus(row["status"])
            if status == current:
                return self._run_snapshot_from_connection(connection, row)
            if status not in _RUN_TRANSITIONS[current]:
                raise InvalidTransitionError(
                    f"cannot transition run from {current.value} to {status.value}"
                )
            connection.execute(
                """
                UPDATE runs
                SET status = ?, final_summary = COALESCE(?, final_summary),
                    error = ?, updated_at = ?
                WHERE run_id = ?
                """,
                (
                    status.value,
                    redact_text(final_summary) if final_summary is not None else None,
                    redact_text(error) if error is not None else None,
                    _dump_datetime(now),
                    run_id,
                ),
            )
            self._append_event(
                connection,
                run_id,
                "run.status_changed",
                {"from": current.value, "to": status.value},
                created_at=now,
            )
        return self.get_run(run_id)

    def set_lead_thread(self, run_id: str, thread_id: str) -> RunSnapshot:
        if not thread_id.strip():
            raise StateError("lead thread ID cannot be empty")
        now = utc_now()
        with self._transaction() as connection:
            self._get_run_row(connection, run_id)
            connection.execute(
                "UPDATE runs SET lead_thread_id = ?, updated_at = ? WHERE run_id = ?",
                (thread_id, _dump_datetime(now), run_id),
            )
            self._append_event(
                connection,
                run_id,
                "run.lead_thread_updated",
                {"thread_id": thread_id},
                created_at=now,
            )
        return self.get_run(run_id)

    def claim_task(
        self,
        run_id: str,
        executor: Executor,
        claimed_by: str,
        *,
        task_id: str | None = None,
    ) -> TaskState | None:
        claimed_by = redact_text(claimed_by)
        if not claimed_by.strip():
            raise StateError("claimant cannot be empty")
        now = utc_now()
        with self._transaction(immediate=True) as connection:
            run_row = self._get_run_row(connection, run_id)
            if RunStatus(run_row["status"]) != RunStatus.RUNNING:
                return None
            self._refresh_ready_tasks(connection, run_id, now)
            parameters: list[str] = [run_id, executor.value, TaskStatus.READY.value]
            task_filter = ""
            if task_id is not None:
                task_filter = " AND task_id = ?"
                parameters.append(task_id)
            row = connection.execute(
                f"""
                SELECT * FROM tasks
                WHERE run_id = ? AND executor = ? AND status = ?{task_filter}
                ORDER BY position, task_id
                LIMIT 1
                """,
                parameters,
            ).fetchone()
            if row is None:
                return None
            next_attempt = int(row["attempt"]) + 1
            connection.execute(
                """
                UPDATE tasks
                SET status = ?, attempt = ?, claimed_by = ?, result_json = NULL,
                    result_kind = NULL, commit_sha = NULL, error = NULL, updated_at = ?
                WHERE run_id = ? AND task_id = ? AND status = ?
                """,
                (
                    TaskStatus.RUNNING.value,
                    next_attempt,
                    claimed_by,
                    _dump_datetime(now),
                    run_id,
                    row["task_id"],
                    TaskStatus.READY.value,
                ),
            )
            claimed = self._get_task_row(connection, run_id, str(row["task_id"]))
            self._append_event(
                connection,
                run_id,
                "task.claimed",
                {
                    "executor": executor.value,
                    "claimed_by": claimed_by,
                    "attempt": next_attempt,
                },
                task_id=str(row["task_id"]),
                created_at=now,
            )
            return self._task_state(claimed)

    def claim_host_task(
        self,
        run_id: str,
        claimed_by: str,
        *,
        task_id: str | None = None,
    ) -> TaskState | None:
        return self.claim_task(run_id, Executor.HOST, claimed_by, task_id=task_id)

    def submit_task_result(
        self,
        run_id: str,
        task_id: str,
        result: WorkerResult,
        *,
        claimed_by: str | None = None,
        worktree_path: str | None = None,
        commit_sha: str | None = None,
    ) -> TaskState:
        now = utc_now()
        result = redact_model(result)
        claimed_by = redact_text(claimed_by) if claimed_by is not None else None
        with self._transaction(immediate=True) as connection:
            self._get_run_row(connection, run_id)
            row = self._get_task_row(connection, run_id, task_id)
            current = TaskStatus(row["status"])
            if current != TaskStatus.RUNNING:
                raise InvalidTransitionError(
                    f"cannot submit task result while task is {current.value}"
                )
            current_claimant = row["claimed_by"]
            if claimed_by is not None and current_claimant != claimed_by:
                raise StateError(
                    f"task is claimed by another worker: {task_id}",
                    detail=f"expected={current_claimant}, received={claimed_by}",
                )

            spec = TaskSpec.model_validate_json(row["spec_json"])
            resolved_commit = commit_sha
            if isinstance(result, HostTaskResult) and result.commit_sha is not None:
                if resolved_commit is not None and resolved_commit != result.commit_sha:
                    raise StateError("conflicting commit SHAs in task result")
                resolved_commit = result.commit_sha
            if (
                result.outcome == TaskOutcome.SUCCEEDED
                and spec.mutates_workspace
                and resolved_commit is None
            ):
                raise StateError("successful mutating tasks must provide a commit SHA")

            next_status = self._result_status(
                result.outcome,
                attempt=int(row["attempt"]),
                max_attempts=spec.max_attempts,
            )
            result_kind = "host" if isinstance(result, HostTaskResult) else "worker"
            connection.execute(
                """
                UPDATE tasks
                SET status = ?, result_json = ?, result_kind = ?, worktree_path = ?,
                    commit_sha = ?, error = ?, updated_at = ?
                WHERE run_id = ? AND task_id = ?
                """,
                (
                    next_status.value,
                    result.model_dump_json(),
                    result_kind,
                    worktree_path,
                    resolved_commit,
                    result.error,
                    _dump_datetime(now),
                    run_id,
                    task_id,
                ),
            )
            self._append_event(
                connection,
                run_id,
                "task.result_submitted",
                {
                    "outcome": result.outcome.value,
                    "status": next_status.value,
                    "attempt": int(row["attempt"]),
                },
                task_id=task_id,
                created_at=now,
            )
            self._refresh_ready_tasks(connection, run_id, now)
            self._sync_run_from_tasks(connection, run_id, now)
            updated = self._get_task_row(connection, run_id, task_id)
            return self._task_state(updated)

    def set_task_worktree(self, run_id: str, task_id: str, path: Path) -> TaskState:
        now = utc_now()
        with self._transaction() as connection:
            self._get_run_row(connection, run_id)
            row = self._get_task_row(connection, run_id, task_id)
            status = TaskStatus(row["status"])
            if status != TaskStatus.RUNNING:
                raise InvalidTransitionError(
                    f"cannot assign a worktree while task is {status.value}"
                )
            connection.execute(
                """
                UPDATE tasks SET worktree_path = ?, updated_at = ?
                WHERE run_id = ? AND task_id = ?
                """,
                (str(path), _dump_datetime(now), run_id, task_id),
            )
            self._append_event(
                connection,
                run_id,
                "task.worktree_assigned",
                {"path": str(path)},
                task_id=task_id,
                created_at=now,
            )
            return self._task_state(self._get_task_row(connection, run_id, task_id))

    def cancel_run(self, run_id: str) -> RunSnapshot:
        now = utc_now()
        with self._transaction(immediate=True) as connection:
            row = self._get_run_row(connection, run_id)
            current = RunStatus(row["status"])
            if current in _TERMINAL_RUN_STATUSES:
                return self._run_snapshot_from_connection(connection, row)
            connection.execute(
                "UPDATE runs SET status = ?, updated_at = ? WHERE run_id = ?",
                (RunStatus.CANCELLED.value, _dump_datetime(now), run_id),
            )
            connection.execute(
                f"""
                UPDATE tasks
                SET status = ?, updated_at = ?
                WHERE run_id = ? AND status IN ({",".join("?" for _ in _ACTIVE_TASK_STATUSES)})
                """,
                (
                    TaskStatus.CANCELLED.value,
                    _dump_datetime(now),
                    run_id,
                    *(status.value for status in _ACTIVE_TASK_STATUSES),
                ),
            )
            self._append_event(
                connection,
                run_id,
                "run.cancelled",
                {"from": current.value},
                created_at=now,
            )
        return self.get_run(run_id)

    def upsert_capability(self, capability: CapabilitySnapshot) -> CapabilitySnapshot:
        capability = redact_model(capability)
        total_latency = capability.average_latency_seconds * (
            capability.successes + capability.failures
        )
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO capabilities (
                    executor, model, status, successes, failures, total_latency_seconds,
                    cooldown_until, metadata_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(executor, model) DO UPDATE SET
                    status = excluded.status,
                    successes = excluded.successes,
                    failures = excluded.failures,
                    total_latency_seconds = excluded.total_latency_seconds,
                    cooldown_until = excluded.cooldown_until,
                    metadata_json = excluded.metadata_json,
                    updated_at = excluded.updated_at
                """,
                (
                    capability.executor.value,
                    capability.model,
                    capability.status.value,
                    capability.successes,
                    capability.failures,
                    total_latency,
                    _dump_optional_datetime(capability.cooldown_until),
                    json.dumps(capability.metadata, sort_keys=True),
                    _dump_datetime(capability.updated_at),
                ),
            )
        return capability

    def record_capability_result(
        self,
        executor: Executor,
        model: str,
        *,
        succeeded: bool,
        latency_seconds: float,
        status: CapabilityStatus | None = None,
    ) -> CapabilitySnapshot:
        if latency_seconds < 0:
            raise StateError("capability latency cannot be negative")
        now = utc_now()
        with self._transaction(immediate=True) as connection:
            row = connection.execute(
                "SELECT * FROM capabilities WHERE executor = ? AND model = ?",
                (executor.value, model),
            ).fetchone()
            successes = int(row["successes"]) if row is not None else 0
            failures = int(row["failures"]) if row is not None else 0
            total_latency = float(row["total_latency_seconds"]) if row is not None else 0.0
            if succeeded:
                successes += 1
            else:
                failures += 1
            resolved_status = status or (
                CapabilityStatus(row["status"]) if row is not None else CapabilityStatus.HEALTHY
            )
            metadata_json = row["metadata_json"] if row is not None else "{}"
            cooldown_until = row["cooldown_until"] if row is not None else None
            connection.execute(
                """
                INSERT INTO capabilities (
                    executor, model, status, successes, failures, total_latency_seconds,
                    cooldown_until, metadata_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(executor, model) DO UPDATE SET
                    status = excluded.status,
                    successes = excluded.successes,
                    failures = excluded.failures,
                    total_latency_seconds = excluded.total_latency_seconds,
                    updated_at = excluded.updated_at
                """,
                (
                    executor.value,
                    model,
                    resolved_status.value,
                    successes,
                    failures,
                    total_latency + latency_seconds,
                    cooldown_until,
                    metadata_json,
                    _dump_datetime(now),
                ),
            )
        return self.get_capability(executor, model)

    def get_capability(self, executor: Executor, model: str) -> CapabilitySnapshot:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM capabilities WHERE executor = ? AND model = ?",
                (executor.value, model),
            ).fetchone()
        if row is None:
            raise StateError(f"capability not found: {executor.value}/{model}")
        return self._capability_snapshot(row)

    def list_capabilities(self) -> list[CapabilitySnapshot]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM capabilities ORDER BY executor, model"
            ).fetchall()
        return [self._capability_snapshot(row) for row in rows]

    def list_events(self, run_id: str) -> list[dict[str, Any]]:
        with self._connect() as connection:
            self._get_run_row(connection, run_id)
            rows = connection.execute(
                """
                SELECT event_id, run_id, task_id, event_type, payload_json, created_at
                FROM events WHERE run_id = ? ORDER BY event_id
                """,
                (run_id,),
            ).fetchall()
        return [
            {
                "event_id": int(row["event_id"]),
                "run_id": row["run_id"],
                "task_id": row["task_id"],
                "event_type": row["event_type"],
                "payload": json.loads(row["payload_json"]),
                "created_at": _load_datetime(row["created_at"]),
            }
            for row in rows
        ]

    def list_run_events(self, run_id: str) -> list[RunEvent]:
        return [RunEvent.model_validate(event) for event in self.list_events(run_id)]

    def record_event(
        self,
        run_id: str,
        event_type: str,
        payload: Mapping[str, object],
    ) -> None:
        now = utc_now()
        with self._transaction() as connection:
            self._get_run_row(connection, run_id)
            self._append_event(
                connection,
                run_id,
                event_type,
                payload,
                created_at=now,
            )

    def record_provider_usage(self, run_id: str, usage: ProviderUsageRecord) -> None:
        usage = redact_model(usage)
        with self._transaction() as connection:
            self._get_run_row(connection, run_id)
            connection.execute(
                """
                INSERT INTO provider_usage (
                    run_id, task_id, phase, executor, model, attempt, outcome, input_tokens,
                    cached_input_tokens, output_tokens, reasoning_output_tokens,
                    duration_seconds, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    usage.task_id,
                    usage.phase,
                    usage.executor.value,
                    usage.model,
                    usage.attempt,
                    usage.outcome.value if usage.outcome is not None else None,
                    usage.input_tokens,
                    usage.cached_input_tokens,
                    usage.output_tokens,
                    usage.reasoning_output_tokens,
                    usage.duration_seconds,
                    _dump_datetime(usage.created_at),
                ),
            )

    def _get_run_row(self, connection: sqlite3.Connection, run_id: str) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        if row is None:
            raise RunNotFoundError(f"run not found: {run_id}")
        return cast(sqlite3.Row, row)

    def _get_task_row(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        task_id: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM tasks WHERE run_id = ? AND task_id = ?",
            (run_id, task_id),
        ).fetchone()
        if row is None:
            raise TaskNotFoundError(f"task not found: {run_id}/{task_id}")
        return cast(sqlite3.Row, row)

    def _refresh_ready_tasks(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        now: datetime,
    ) -> None:
        rows = connection.execute(
            "SELECT task_id, status FROM tasks WHERE run_id = ? ORDER BY position",
            (run_id,),
        ).fetchall()
        statuses = {str(row["task_id"]): TaskStatus(row["status"]) for row in rows}
        dependency_rows = connection.execute(
            """
            SELECT task_id, depends_on_task_id
            FROM task_dependencies WHERE run_id = ?
            """,
            (run_id,),
        ).fetchall()
        dependencies: dict[str, set[str]] = {task_id: set() for task_id in statuses}
        for row in dependency_rows:
            dependencies[str(row["task_id"])].add(str(row["depends_on_task_id"]))

        changed = True
        while changed:
            changed = False
            for task_id, current in list(statuses.items()):
                if current not in {TaskStatus.PENDING, TaskStatus.RETRYING}:
                    continue
                dependency_statuses = {statuses[dependency] for dependency in dependencies[task_id]}
                if dependency_statuses & _FAILED_DEPENDENCY_STATUSES:
                    next_status = TaskStatus.BLOCKED
                    error = "one or more dependencies did not succeed"
                elif all(status == TaskStatus.SUCCEEDED for status in dependency_statuses):
                    next_status = TaskStatus.READY
                    error = None
                else:
                    continue
                connection.execute(
                    """
                    UPDATE tasks SET status = ?, error = ?, updated_at = ?
                    WHERE run_id = ? AND task_id = ?
                    """,
                    (next_status.value, error, _dump_datetime(now), run_id, task_id),
                )
                self._append_event(
                    connection,
                    run_id,
                    "task.status_changed",
                    {"from": current.value, "to": next_status.value},
                    task_id=task_id,
                    created_at=now,
                )
                statuses[task_id] = next_status
                changed = True

    def _sync_run_from_tasks(
        self,
        connection: sqlite3.Connection,
        run_id: str,
        now: datetime,
    ) -> None:
        run_row = self._get_run_row(connection, run_id)
        if RunStatus(run_row["status"]) != RunStatus.RUNNING:
            return
        task_rows = connection.execute(
            "SELECT status FROM tasks WHERE run_id = ?", (run_id,)
        ).fetchall()
        statuses = [TaskStatus(row["status"]) for row in task_rows]
        if not statuses or any(status in _ACTIVE_TASK_STATUSES for status in statuses):
            return
        if all(status == TaskStatus.SUCCEEDED for status in statuses):
            request = StartRunRequest.model_validate_json(run_row["request_json"])
            next_status = (
                RunStatus.INTEGRATING
                if request.delivery_mode == DeliveryMode.INTEGRATION_BRANCH
                else RunStatus.REVIEWING
            )
        elif any(status == TaskStatus.FAILED for status in statuses):
            next_status = RunStatus.FAILED
        else:
            next_status = RunStatus.BLOCKED
        connection.execute(
            "UPDATE runs SET status = ?, updated_at = ? WHERE run_id = ?",
            (next_status.value, _dump_datetime(now), run_id),
        )
        self._append_event(
            connection,
            run_id,
            "run.status_changed",
            {"from": RunStatus.RUNNING.value, "to": next_status.value},
            created_at=now,
        )

    @staticmethod
    def _result_status(
        outcome: TaskOutcome,
        *,
        attempt: int,
        max_attempts: int,
    ) -> TaskStatus:
        if outcome == TaskOutcome.SUCCEEDED:
            return TaskStatus.SUCCEEDED
        if outcome == TaskOutcome.BLOCKED:
            return TaskStatus.BLOCKED
        if attempt < max_attempts:
            return TaskStatus.RETRYING
        return TaskStatus.FAILED

    def _run_snapshot_from_connection(
        self,
        connection: sqlite3.Connection,
        run_row: sqlite3.Row,
    ) -> RunSnapshot:
        task_rows = connection.execute(
            "SELECT * FROM tasks WHERE run_id = ? ORDER BY position, task_id",
            (run_row["run_id"],),
        ).fetchall()
        usage_rows = connection.execute(
            "SELECT * FROM provider_usage WHERE run_id = ? ORDER BY usage_id",
            (run_row["run_id"],),
        ).fetchall()
        return self._run_snapshot(run_row, task_rows, usage_rows)

    def _run_snapshot(
        self,
        row: sqlite3.Row,
        task_rows: list[sqlite3.Row],
        usage_rows: list[sqlite3.Row] | None = None,
    ) -> RunSnapshot:
        plan_json = row["plan_json"]
        return RunSnapshot(
            run_id=row["run_id"],
            request=StartRunRequest.model_validate_json(row["request_json"]),
            status=RunStatus(row["status"]),
            display_names=(
                LocalizedRunNames.model_validate_json(row["display_names_json"])
                if row["display_names_json"] is not None
                else None
            ),
            execution_settings=(
                RunExecutionSettings.model_validate_json(row["execution_settings_json"])
                if row["execution_settings_json"] is not None
                else None
            ),
            plan=RunPlan.model_validate_json(plan_json) if plan_json is not None else None,
            lead_thread_id=row["lead_thread_id"],
            integration_branch=row["integration_branch"],
            base_branch=row["base_branch"],
            base_commit=row["base_commit"],
            final_summary=row["final_summary"],
            error=row["error"],
            tasks=[self._task_state(task_row) for task_row in task_rows],
            provider_usage=[
                ProviderUsageRecord(
                    phase=usage_row["phase"],
                    executor=Executor(usage_row["executor"]),
                    model=usage_row["model"],
                    task_id=usage_row["task_id"],
                    attempt=(
                        int(usage_row["attempt"]) if usage_row["attempt"] is not None else None
                    ),
                    outcome=(
                        ProviderInvocationOutcome(usage_row["outcome"])
                        if usage_row["outcome"] is not None
                        else None
                    ),
                    input_tokens=int(usage_row["input_tokens"]),
                    cached_input_tokens=int(usage_row["cached_input_tokens"]),
                    output_tokens=int(usage_row["output_tokens"]),
                    reasoning_output_tokens=int(usage_row["reasoning_output_tokens"]),
                    duration_seconds=float(usage_row["duration_seconds"]),
                    created_at=_load_datetime(usage_row["created_at"]),
                )
                for usage_row in usage_rows or []
            ],
            created_at=_load_datetime(row["created_at"]),
            updated_at=_load_datetime(row["updated_at"]),
        )

    @staticmethod
    def _run_summary(row: sqlite3.Row) -> RunSummary:
        request = StartRunRequest.model_validate_json(row["request_json"])
        return RunSummary(
            run_id=row["run_id"],
            workspace=row["workspace"] or request.workspace or "",
            goal=request.goal,
            display_names=(
                LocalizedRunNames.model_validate_json(row["display_names_json"])
                if row["display_names_json"] is not None
                else None
            ),
            status=RunStatus(row["status"]),
            delivery_mode=request.delivery_mode,
            base_branch=row["base_branch"],
            task_count=int(row["task_count"]),
            succeeded_task_count=int(row["succeeded_task_count"]),
            failed_task_count=int(row["failed_task_count"]),
            observed_total_tokens=int(row["observed_total_tokens"]),
            retry_tokens=int(row["retry_tokens"]),
            created_at=_load_datetime(row["created_at"]),
            updated_at=_load_datetime(row["updated_at"]),
        )

    @staticmethod
    def _task_state(row: sqlite3.Row) -> TaskState:
        result_json = row["result_json"]
        result: WorkerResult | HostTaskResult | None = None
        if result_json is not None:
            result = (
                HostTaskResult.model_validate_json(result_json)
                if row["result_kind"] == "host"
                else WorkerResult.model_validate_json(result_json)
            )
        return TaskState(
            run_id=row["run_id"],
            spec=TaskSpec.model_validate_json(row["spec_json"]),
            status=TaskStatus(row["status"]),
            executor=Executor(row["executor"]),
            assignment_rationale=row["assignment_rationale"],
            attempt=int(row["attempt"]),
            claimed_by=row["claimed_by"],
            result=result,
            worktree_path=row["worktree_path"],
            commit_sha=row["commit_sha"],
            error=row["error"],
            created_at=_load_datetime(row["created_at"]),
            updated_at=_load_datetime(row["updated_at"]),
        )

    @staticmethod
    def _capability_snapshot(row: sqlite3.Row) -> CapabilitySnapshot:
        attempts = int(row["successes"]) + int(row["failures"])
        average_latency = float(row["total_latency_seconds"]) / attempts if attempts else 0.0
        return CapabilitySnapshot(
            executor=Executor(row["executor"]),
            model=row["model"],
            status=CapabilityStatus(row["status"]),
            successes=int(row["successes"]),
            failures=int(row["failures"]),
            average_latency_seconds=average_latency,
            cooldown_until=_load_optional_datetime(row["cooldown_until"]),
            metadata=json.loads(row["metadata_json"]),
            updated_at=_load_datetime(row["updated_at"]),
        )

    @staticmethod
    def _append_event(
        connection: sqlite3.Connection,
        run_id: str,
        event_type: str,
        payload: Mapping[str, object],
        *,
        task_id: str | None = None,
        created_at: datetime,
    ) -> None:
        connection.execute(
            """
            INSERT INTO events (run_id, task_id, event_type, payload_json, created_at)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                run_id,
                task_id,
                event_type,
                json.dumps(redact_value(dict(payload)), sort_keys=True),
                _dump_datetime(created_at),
            ),
        )


def _dump_datetime(value: datetime) -> str:
    return value.isoformat()


def _dump_optional_datetime(value: datetime | None) -> str | None:
    return _dump_datetime(value) if value is not None else None


def _load_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _load_optional_datetime(value: str | None) -> datetime | None:
    return _load_datetime(value) if value is not None else None
