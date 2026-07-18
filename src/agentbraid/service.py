from __future__ import annotations

import asyncio
import os
from collections.abc import Coroutine
from contextlib import suppress
from dataclasses import replace
from pathlib import Path
from typing import Any, Protocol, TypeVar

from agentbraid.config import AgentBraidConfig
from agentbraid.errors import (
    ProviderError,
    ProviderUnavailableError,
    RoutingError,
    SecurityBoundaryError,
    StateError,
    WorktreeConflictError,
    WorktreeError,
)
from agentbraid.models import (
    ApplyReadiness,
    ApplyRunResult,
    CapabilitySnapshot,
    CapabilityStatus,
    CodexReasoningEffort,
    DeliveryMode,
    Executor,
    HostTaskResult,
    LocalizedRunNames,
    ProviderInvocationOutcome,
    ProviderUsageRecord,
    RoutingMode,
    RunCleanupPreview,
    RunCleanupResult,
    RunExecutionOverrides,
    RunExecutionSettings,
    RunPlan,
    RunReview,
    RunSnapshot,
    RunStatus,
    StartRunRequest,
    TaskOutcome,
    TaskSpec,
    TaskState,
    TaskStatus,
    WorkerResult,
    WorkspaceMode,
    utc_now,
)
from agentbraid.providers.base import StructuredProviderResult
from agentbraid.providers.codex import CodexAdapter
from agentbraid.redaction import redact_model, redact_text
from agentbraid.router import TaskRouter
from agentbraid.security import assert_safe_runtime_paths
from agentbraid.store import StateStore
from agentbraid.worktrees import WorktreeManager

_InvocationOutput = TypeVar("_InvocationOutput")
_CANCELLATION_POLL_SECONDS = 0.25


class _RunCancelled(Exception):
    pass


class CodexProvider(Protocol):
    async def plan(
        self,
        request: StartRunRequest,
        workspace: Path,
    ) -> StructuredProviderResult[RunPlan]: ...

    async def execute_task(
        self,
        task: TaskSpec,
        workspace: Path,
        *,
        run_id: str,
    ) -> StructuredProviderResult[WorkerResult]: ...

    async def review_run(
        self,
        run: RunSnapshot,
        workspace: Path,
    ) -> StructuredProviderResult[RunReview]: ...


class AgentBraidService:
    """Application boundary shared by MCP transports and local commands."""

    def __init__(
        self,
        config: AgentBraidConfig,
        workspace: Path,
        *,
        store: StateStore | None = None,
        codex: CodexProvider | None = None,
        router: TaskRouter | None = None,
        worktrees: WorktreeManager | None = None,
    ) -> None:
        self.config = config
        self.workspace = workspace.expanduser().resolve()
        assert_safe_runtime_paths(
            self.workspace,
            config.state_dir,
            config.database_path,
            config.worktree_dir,
        )
        self.store = store or StateStore(config.database_path)
        self.codex = codex or CodexAdapter(config)
        self._codex_override = codex
        self.router = router or TaskRouter()
        self.worktrees = worktrees or WorktreeManager(self.workspace, config.worktree_dir)
        self._worktrees_override = worktrees
        self._drain_locks: dict[str, asyncio.Lock] = {}
        self._finalize_locks: dict[str, asyncio.Lock] = {}
        self._integration_locks: dict[str, asyncio.Lock] = {}
        self._active_invocations: dict[
            tuple[str, str], tuple[asyncio.AbstractEventLoop, asyncio.Task[Any]]
        ] = {}

    @classmethod
    def from_workspace(cls, workspace: Path) -> AgentBraidService:
        resolved = workspace.expanduser().resolve()
        config = AgentBraidConfig.load(resolved)
        store = StateStore(config.database_path)
        saved = store.get_workspace_settings(str(resolved))
        if saved is not None:
            config = config.with_workspace_runtime(saved)
        assert_safe_runtime_paths(
            resolved,
            config.state_dir,
            config.database_path,
            config.worktree_dir,
        )
        config.ensure_directories()
        return cls(config, resolved, store=store)

    async def start_run(self, request: StartRunRequest) -> RunSnapshot:
        run = self.create_run(request)
        return await self.execute_run(run.run_id)

    def create_run(self, request: StartRunRequest) -> RunSnapshot:
        _assert_not_child()
        settings = self._resolve_execution_settings(request)
        worktrees = self._worktrees_for_settings(settings)
        worktrees.assert_clean_workspace()
        target = worktrees.primary_target()
        normalized_request = redact_model(
            self._normalize_request(request).model_copy(
                update={
                    "host_model": settings.host_model,
                    "delivery_mode": settings.delivery_mode,
                    "execution": RunExecutionOverrides(
                        codex_model=settings.codex_model,
                        codex_reasoning_effort=settings.codex_reasoning_effort,
                        host_model=settings.host_model,
                        routing_mode=settings.routing_mode,
                        delivery_mode=settings.delivery_mode,
                        workspace_mode=settings.workspace_mode,
                        max_parallel_codex=settings.max_parallel_codex,
                        max_task_attempts=settings.max_task_attempts,
                        codex_timeout_seconds=settings.codex_timeout_seconds,
                        max_output_bytes=settings.max_output_bytes,
                    ),
                }
            )
        )
        return self.store.create_run(
            normalized_request,
            base_branch=target.branch,
            base_commit=target.commit,
            execution_settings=settings,
        )

    async def execute_run(self, run_id: str) -> RunSnapshot:
        run = self.store.get_run(run_id)
        if run.status != RunStatus.CREATED:
            raise StateError(f"run must be created before execution: {run.status.value}")
        settings = self._settings_for_run(run)
        worktrees = self._worktrees_for_run(run)
        codex = self._codex_for_run(run)
        target = worktrees.primary_target()
        if run.base_branch != target.branch or run.base_commit != target.commit:
            self.store.set_run_status(
                run.run_id,
                RunStatus.FAILED,
                error="primary workspace changed before planning started",
            )
            raise WorktreeError("primary workspace changed before planning started")
        self.store.begin_planning(run.run_id)
        model = settings.codex_model or "codex-default"
        try:
            planning = await self._await_invocation(
                run.run_id,
                "planning",
                codex.plan(run.request, self.workspace),
            )
            if self.store.get_run_status(run.run_id) == RunStatus.CANCELLED:
                raise _RunCancelled
            self.store.record_provider_usage(
                run.run_id,
                _provider_usage_record(
                    "planning",
                    model,
                    planning,
                    reasoning_effort=settings.codex_reasoning_effort,
                    outcome=ProviderInvocationOutcome.SUCCEEDED,
                ),
            )
            self.store.record_capability_result(
                Executor.CODEX,
                model,
                succeeded=True,
                latency_seconds=planning.duration_seconds,
                status=CapabilityStatus.HEALTHY,
            )
            display_names = planning.output.display_names or _fallback_display_names(
                run.request.goal
            )
            plan = planning.output.model_copy(
                update={
                    "display_names": display_names,
                    "tasks": [
                        task.model_copy(
                            update={
                                "max_attempts": min(
                                    task.max_attempts,
                                    settings.max_task_attempts,
                                )
                            }
                        )
                        for task in planning.output.tasks
                    ],
                }
            )
            if settings.workspace_mode == WorkspaceMode.READ_ONLY and any(
                task.mutates_workspace for task in plan.tasks
            ):
                raise RoutingError("read-only runs cannot contain mutating tasks")
            assignments = self.router.route_plan(
                plan,
                self.store.list_capabilities(),
                codex_model=model,
                host_model=settings.host_model,
                allowed_executors=(
                    frozenset({Executor.CODEX})
                    if settings.routing_mode == RoutingMode.CODEX_ONLY
                    else frozenset({Executor.CODEX, Executor.HOST})
                ),
            )
            integration_branch = f"agentbraid/integration/{run.run_id[:12]}"
            current_target = worktrees.primary_target()
            if current_target != target:
                raise WorktreeError("primary workspace changed while the run was being planned")
            worktrees.prepare_run(
                run.run_id,
                integration_branch,
                base_commit=target.commit,
            )
            snapshot = self.store.save_plan(
                run.run_id,
                plan,
                assignments,
                lead_thread_id=planning.thread_id,
                integration_branch=integration_branch,
            )
        except _RunCancelled:
            return self.store.get_run(run.run_id)
        except ProviderError as exc:
            status = (
                CapabilityStatus.CONSTRAINED if exc.quota_limited else CapabilityStatus.UNAVAILABLE
            )
            self.store.record_capability_result(
                Executor.CODEX,
                model,
                succeeded=False,
                latency_seconds=0,
                status=status,
            )
            self.store.set_run_status(run.run_id, RunStatus.FAILED, error=exc.message)
            raise
        except RoutingError as exc:
            self.store.set_run_status(run.run_id, RunStatus.BLOCKED, error=exc.message)
            raise
        except WorktreeError as exc:
            self.store.set_run_status(run.run_id, RunStatus.FAILED, error=exc.message)
            raise
        await self._drain_codex_tasks(snapshot.run_id)
        await self._finalize_if_ready(snapshot.run_id)
        return self.store.get_run(snapshot.run_id)

    def claim_host_task(
        self,
        run_id: str,
        host_id: str,
        *,
        task_id: str | None = None,
    ) -> TaskState | None:
        safe_host_id = redact_text(host_id)
        claimed = self.store.claim_host_task(run_id, safe_host_id, task_id=task_id)
        if claimed is not None:
            run = self.store.get_run(run_id)
            worktrees = self._worktrees_for_run(run)
            integration_branch = _integration_branch(run)
            try:
                if claimed.spec.mutates_workspace:
                    worktree = worktrees.prepare_task(
                        run_id,
                        claimed.spec.task_id,
                        integration_branch,
                    )
                else:
                    worktree = worktrees.prepare_run(run_id, integration_branch)
                claimed = self.store.set_task_worktree(
                    run_id,
                    claimed.spec.task_id,
                    worktree.path,
                )
            except WorktreeError as exc:
                self.store.submit_task_result(
                    run_id,
                    claimed.spec.task_id,
                    HostTaskResult(
                        outcome=TaskOutcome.FAILED,
                        summary="Host worktree could not be prepared.",
                        error=exc.message,
                    ),
                    claimed_by=safe_host_id,
                )
                raise
            self._touch_host_capability(run_id, safe_host_id)
        return claimed

    async def submit_host_result(
        self,
        run_id: str,
        task_id: str,
        host_id: str,
        result: HostTaskResult,
    ) -> TaskState:
        safe_host_id = redact_text(host_id)
        task = _task_by_id(self.store.get_run(run_id), task_id)
        run = self.store.get_run(run_id)
        worktrees = self._worktrees_for_run(run)
        if task.status != TaskStatus.RUNNING or task.executor != Executor.HOST:
            raise StateError(f"host task is not running: {run_id}/{task_id}")
        if task.claimed_by != safe_host_id:
            raise StateError(f"host task is claimed by another worker: {run_id}/{task_id}")
        commit_sha = result.commit_sha
        if result.outcome == TaskOutcome.SUCCEEDED and task.spec.mutates_workspace:
            _require_success_evidence(task.spec, result)
            if commit_sha is None:
                raise StateError("successful mutating host tasks must provide a commit SHA")
            try:
                worktrees.integrate_task(
                    run_id,
                    task_id,
                    _integration_branch(run),
                    commit_sha,
                )
            except WorktreeError as exc:
                result = result.model_copy(
                    update={
                        "outcome": (
                            TaskOutcome.BLOCKED
                            if isinstance(exc, WorktreeConflictError)
                            else TaskOutcome.FAILED
                        ),
                        "summary": "Host task commit could not be safely integrated.",
                        "error": exc.message,
                    }
                )
        elif not task.spec.mutates_workspace and commit_sha is not None:
            raise StateError("non-mutating host tasks must not submit a commit SHA")
        elif not task.spec.mutates_workspace and task.worktree_path is not None:
            try:
                worktrees.assert_clean_worktree(Path(task.worktree_path))
            except WorktreeError as exc:
                raise StateError(
                    "non-mutating host task changed its read-only worktree",
                    detail=exc.detail,
                ) from exc
        completed = self.store.submit_task_result(
            run_id,
            task_id,
            result,
            claimed_by=safe_host_id,
            worktree_path=task.worktree_path,
            commit_sha=commit_sha,
        )
        self.store.record_capability_result(
            Executor.HOST,
            self._settings_for_run(run).host_model,
            succeeded=result.outcome == TaskOutcome.SUCCEEDED,
            latency_seconds=0,
            status=(
                CapabilityStatus.HEALTHY
                if result.outcome == TaskOutcome.SUCCEEDED
                else CapabilityStatus.CONSTRAINED
            ),
        )
        await self._drain_codex_tasks(run_id)
        await self._finalize_if_ready(run_id)
        return completed

    def get_run(self, run_id: str) -> RunSnapshot:
        return self.store.get_run(run_id)

    def cancel_run(self, run_id: str) -> RunSnapshot:
        cancelled = self.store.cancel_run(run_id)
        for (candidate_run_id, _), (loop, task) in list(self._active_invocations.items()):
            if candidate_run_id == run_id and not task.done():
                loop.call_soon_threadsafe(task.cancel)
        return cancelled

    def list_capabilities(self) -> list[CapabilitySnapshot]:
        return self.store.list_capabilities()

    def resolve_execution_settings(self, request: StartRunRequest) -> RunExecutionSettings:
        return self._resolve_execution_settings(request)

    def preview_run_cleanup(self, run_id: str) -> RunCleanupPreview:
        run = self.store.get_run(run_id)
        return self._worktrees_for_run(run).preview_run_cleanup(run)

    def delete_run(self, run_id: str) -> RunCleanupResult:
        run = self.store.get_run(run_id)
        preview = self._worktrees_for_run(run).preview_run_cleanup(run)
        if not preview.deletable:
            return RunCleanupResult(
                run_id=run_id,
                deleted=False,
                blockers=preview.blockers,
            )
        self._worktrees_for_run(run).cleanup_run_artifacts(run)
        self.store.delete_run_record(run_id)
        return RunCleanupResult(run_id=run_id, deleted=True)

    def get_apply_readiness(self, run_id: str) -> ApplyReadiness:
        run = self.store.get_run(run_id)
        worktrees = self._worktrees_for_run(run)
        blockers: list[str] = []
        if run.request.delivery_mode != DeliveryMode.INTEGRATION_BRANCH:
            blockers.append("report-only runs cannot be applied")
        if run.status != RunStatus.COMPLETED:
            blockers.append(f"run status is {run.status.value}, not completed")
        if run.integration_branch is None:
            blockers.append("run has no integration branch")
        if run.base_branch is None or run.base_commit is None:
            blockers.append("run is missing its original delivery target")

        current_branch: str | None = None
        current_commit: str | None = None
        try:
            target = worktrees.primary_target()
            current_branch = target.branch
            current_commit = target.commit
            if not blockers:
                assert run.integration_branch is not None
                assert run.base_branch is not None
                assert run.base_commit is not None
                worktrees.validate_apply_target(
                    run.integration_branch,
                    expected_branch=run.base_branch,
                    expected_commit=run.base_commit,
                )
        except WorktreeError as exc:
            blockers.append(exc.message)
        return ApplyReadiness(
            can_apply=not blockers,
            blockers=blockers,
            expected_branch=run.base_branch,
            expected_commit=run.base_commit,
            current_branch=current_branch,
            current_commit=current_commit,
        )

    def apply_run(self, run_id: str, confirmation: str) -> ApplyRunResult:
        if confirmation != "apply-reviewed-run":
            raise SecurityBoundaryError(
                "apply_run requires the explicit confirmation phrase: apply-reviewed-run"
            )
        run = self.store.get_run(run_id)
        worktrees = self._worktrees_for_run(run)
        if run.request.delivery_mode != DeliveryMode.INTEGRATION_BRANCH:
            raise StateError("report-only runs cannot be applied to the primary workspace")
        if run.status != RunStatus.COMPLETED:
            raise StateError(f"run must complete final review before apply: {run.status.value}")
        integration_branch = _integration_branch(run)
        if run.base_branch is None or run.base_commit is None:
            raise StateError("run is missing its original delivery target")
        commit_sha = worktrees.apply_integration_to_target(
            integration_branch,
            expected_branch=run.base_branch,
            expected_commit=run.base_commit,
        )
        self.store.record_event(
            run_id,
            "run.applied",
            {"integration_branch": integration_branch, "commit_sha": commit_sha},
        )
        return ApplyRunResult(
            run_id=run_id,
            integration_branch=integration_branch,
            commit_sha=commit_sha,
        )

    async def _await_invocation(
        self,
        run_id: str,
        invocation_name: str,
        invocation: Coroutine[Any, Any, _InvocationOutput],
    ) -> _InvocationOutput:
        invocation_task = asyncio.create_task(invocation)
        cancellation_task = asyncio.create_task(self._wait_for_persisted_cancellation(run_id))
        key = (run_id, invocation_name)
        self._active_invocations[key] = (asyncio.get_running_loop(), invocation_task)
        try:
            done, _ = await asyncio.wait(
                {invocation_task, cancellation_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if cancellation_task in done:
                invocation_task.cancel()
                with suppress(asyncio.CancelledError):
                    await invocation_task
                raise _RunCancelled
            try:
                output = await invocation_task
                if self.store.get_run_status(run_id) == RunStatus.CANCELLED:
                    raise _RunCancelled
                return output
            except asyncio.CancelledError:
                if self.store.get_run_status(run_id) == RunStatus.CANCELLED:
                    raise _RunCancelled from None
                raise
        except asyncio.CancelledError:
            invocation_task.cancel()
            with suppress(asyncio.CancelledError):
                await invocation_task
            raise
        finally:
            cancellation_task.cancel()
            with suppress(asyncio.CancelledError):
                await cancellation_task
            self._active_invocations.pop(key, None)

    async def _wait_for_persisted_cancellation(self, run_id: str) -> None:
        while self.store.get_run_status(run_id) != RunStatus.CANCELLED:
            await asyncio.sleep(_CANCELLATION_POLL_SECONDS)

    async def _drain_codex_tasks(self, run_id: str) -> None:
        async with self._lock(self._drain_locks, run_id):
            run = self.store.get_run(run_id)
            settings = self._settings_for_run(run)
            model = settings.codex_model or "codex-default"
            while self.store.get_run(run_id).status == RunStatus.RUNNING:
                claimed: list[TaskState] = []
                for _ in range(settings.max_parallel_codex):
                    task = self.store.claim_task(
                        run_id,
                        Executor.CODEX,
                        "agentbraid-codex-worker",
                    )
                    if task is None:
                        break
                    claimed.append(task)
                if not claimed:
                    return
                await asyncio.gather(
                    *(self._execute_codex_task(run_id, task, model) for task in claimed)
                )

    async def _execute_codex_task(
        self,
        run_id: str,
        task: TaskState,
        model: str,
    ) -> None:
        run = self.store.get_run(run_id)
        settings = self._settings_for_run(run)
        worktrees = self._worktrees_for_run(run)
        codex = self._codex_for_run(run)
        integration_branch = _integration_branch(run)
        worktree_path = Path(task.worktree_path) if task.worktree_path else None
        commit_sha: str | None = None
        try:
            async with self._lock(self._integration_locks, run_id):
                if task.spec.mutates_workspace:
                    worktree = worktrees.prepare_task(
                        run_id,
                        task.spec.task_id,
                        integration_branch,
                    )
                else:
                    worktree = worktrees.prepare_run(run_id, integration_branch)
            worktree_path = worktree.path
            task = self.store.set_task_worktree(run_id, task.spec.task_id, worktree.path)
            invocation = await self._await_invocation(
                run_id,
                task.spec.task_id,
                codex.execute_task(task.spec, worktree.path, run_id=run_id),
            )
            if self.store.get_run_status(run_id) == RunStatus.CANCELLED:
                raise _RunCancelled
            result = invocation.output
            self.store.record_provider_usage(
                run_id,
                _provider_usage_record(
                    "task",
                    model,
                    invocation,
                    reasoning_effort=settings.codex_reasoning_effort,
                    task_id=task.spec.task_id,
                    attempt=task.attempt,
                    outcome=ProviderInvocationOutcome(result.outcome.value),
                ),
            )
            if result.outcome == TaskOutcome.SUCCEEDED and task.spec.mutates_workspace:
                _require_success_evidence(task.spec, result)
                async with self._lock(self._integration_locks, run_id):
                    commit_sha = worktrees.commit_task(
                        worktree.path,
                        task.spec.task_id,
                        task.spec.title,
                    )
                    worktrees.integrate_task(
                        run_id,
                        task.spec.task_id,
                        integration_branch,
                        commit_sha,
                    )
            self.store.record_capability_result(
                Executor.CODEX,
                model,
                succeeded=result.outcome == TaskOutcome.SUCCEEDED,
                latency_seconds=invocation.duration_seconds,
                status=CapabilityStatus.HEALTHY,
            )
        except _RunCancelled:
            return
        except ProviderError as exc:
            result = WorkerResult(
                outcome=(
                    TaskOutcome.BLOCKED
                    if isinstance(exc, ProviderUnavailableError)
                    and (exc.quota_limited or not exc.retryable)
                    else TaskOutcome.FAILED
                ),
                summary="Codex worker invocation failed.",
                error=exc.message,
            )
            self.store.record_capability_result(
                Executor.CODEX,
                model,
                succeeded=False,
                latency_seconds=0,
                status=(
                    CapabilityStatus.CONSTRAINED
                    if exc.quota_limited
                    else CapabilityStatus.UNAVAILABLE
                ),
            )
        except (StateError, WorktreeError) as exc:
            result = WorkerResult(
                outcome=(
                    TaskOutcome.BLOCKED
                    if isinstance(exc, WorktreeConflictError)
                    else TaskOutcome.FAILED
                ),
                summary="Codex worker output could not be safely integrated.",
                error=exc.message,
            )
            self.store.record_capability_result(
                Executor.CODEX,
                model,
                succeeded=False,
                latency_seconds=0,
                status=CapabilityStatus.CONSTRAINED,
            )
        if self.store.get_run(run_id).status == RunStatus.CANCELLED:
            return
        self.store.submit_task_result(
            run_id,
            task.spec.task_id,
            result,
            claimed_by="agentbraid-codex-worker",
            worktree_path=str(worktree_path) if worktree_path is not None else None,
            commit_sha=commit_sha,
        )

    async def _finalize_if_ready(self, run_id: str) -> None:
        async with self._lock(self._finalize_locks, run_id):
            run = self.store.get_run(run_id)
            worktrees = self._worktrees_for_run(run)
            codex = self._codex_for_run(run)
            if run.status not in {RunStatus.INTEGRATING, RunStatus.REVIEWING}:
                return
            try:
                async with self._lock(self._integration_locks, run_id):
                    integration = worktrees.prepare_run(run_id, _integration_branch(run))
            except WorktreeError as exc:
                self.store.set_run_status(
                    run_id,
                    RunStatus.BLOCKED,
                    error=f"final review workspace is unavailable: {exc.message}",
                )
                return
            if run.status == RunStatus.INTEGRATING:
                self.store.set_run_status(run_id, RunStatus.REVIEWING)
            reviewing = self.store.get_run(run_id)
            settings = self._settings_for_run(run)
            model = settings.codex_model or "codex-default"
            try:
                invocation = await self._await_invocation(
                    run_id,
                    "final-review",
                    codex.review_run(reviewing, integration.path),
                )
                if self.store.get_run_status(run_id) == RunStatus.CANCELLED:
                    raise _RunCancelled
            except _RunCancelled:
                return
            except ProviderError as exc:
                self.store.record_capability_result(
                    Executor.CODEX,
                    model,
                    succeeded=False,
                    latency_seconds=0,
                    status=(
                        CapabilityStatus.CONSTRAINED
                        if exc.quota_limited
                        else CapabilityStatus.UNAVAILABLE
                    ),
                )
                self.store.set_run_status(
                    run_id,
                    RunStatus.BLOCKED,
                    error=f"final review failed: {exc.message}",
                )
                return
            review = invocation.output
            self.store.record_provider_usage(
                run_id,
                _provider_usage_record(
                    "review",
                    model,
                    invocation,
                    reasoning_effort=settings.codex_reasoning_effort,
                    outcome=(
                        ProviderInvocationOutcome.APPROVED
                        if review.approved
                        else ProviderInvocationOutcome.REJECTED
                    ),
                ),
            )
            self.store.record_capability_result(
                Executor.CODEX,
                model,
                succeeded=review.approved,
                latency_seconds=invocation.duration_seconds,
                status=CapabilityStatus.HEALTHY,
            )
            self.store.record_event(
                run_id,
                "run.reviewed",
                review.model_dump(mode="json"),
            )
            if review.approved:
                self.store.set_run_status(
                    run_id,
                    RunStatus.COMPLETED,
                    final_summary=review.summary,
                )
            else:
                self.store.set_run_status(
                    run_id,
                    RunStatus.BLOCKED,
                    final_summary=review.summary,
                    error="Codex lead did not approve the integrated candidate",
                )

    @staticmethod
    def _lock(locks: dict[str, asyncio.Lock], run_id: str) -> asyncio.Lock:
        return locks.setdefault(run_id, asyncio.Lock())

    def _touch_host_capability(self, run_id: str, host_id: str) -> None:
        run = self.store.get_run(run_id)
        model = self._settings_for_run(run).host_model
        try:
            current = self.store.get_capability(Executor.HOST, model)
        except StateError:
            current = CapabilitySnapshot(executor=Executor.HOST, model=model)
        metadata = {**current.metadata, "last_host_id": host_id}
        self.store.upsert_capability(
            current.model_copy(
                update={
                    "status": CapabilityStatus.HEALTHY,
                    "metadata": metadata,
                    "updated_at": utc_now(),
                }
            )
        )

    def _resolve_execution_settings(self, request: StartRunRequest) -> RunExecutionSettings:
        defaults = self.config.default_workspace_settings(self.workspace)
        saved = self.store.get_workspace_settings(str(self.workspace)) or defaults
        overrides = request.execution

        codex_model = overrides.codex_model if overrides is not None else None
        if codex_model is None:
            codex_model = saved.codex_model
        if "AGENTBRAID_CODEX_MODEL" in os.environ:
            codex_model = self.config.codex_model

        codex_reasoning_effort = overrides.codex_reasoning_effort if overrides is not None else None
        if codex_reasoning_effort is None:
            codex_reasoning_effort = saved.codex_reasoning_effort
        if "AGENTBRAID_CODEX_REASONING_EFFORT" in os.environ:
            codex_reasoning_effort = self.config.codex_reasoning_effort

        host_model = saved.host_model
        if request.host_model != "antigravity-auto":
            host_model = request.host_model
        if overrides is not None and overrides.host_model is not None:
            host_model = overrides.host_model

        delivery_mode = saved.delivery_mode
        if request.delivery_mode != DeliveryMode.INTEGRATION_BRANCH:
            delivery_mode = request.delivery_mode
        if overrides is not None and overrides.delivery_mode is not None:
            delivery_mode = overrides.delivery_mode

        settings = RunExecutionSettings(
            codex_binary=self.config.codex_binary,
            codex_model=codex_model,
            codex_reasoning_effort=codex_reasoning_effort,
            host_model=host_model,
            routing_mode=(
                overrides.routing_mode
                if overrides is not None and overrides.routing_mode is not None
                else saved.routing_mode
            ),
            delivery_mode=delivery_mode,
            workspace_mode=(
                overrides.workspace_mode
                if overrides is not None and overrides.workspace_mode is not None
                else saved.workspace_mode
            ),
            max_parallel_codex=_resolved_integer_setting(
                overrides.max_parallel_codex if overrides is not None else None,
                saved.max_parallel_codex,
                defaults.max_parallel_codex,
                "AGENTBRAID_MAX_PARALLEL_CODEX",
            ),
            max_task_attempts=_resolved_integer_setting(
                overrides.max_task_attempts if overrides is not None else None,
                saved.max_task_attempts,
                defaults.max_task_attempts,
                "AGENTBRAID_MAX_TASK_ATTEMPTS",
            ),
            codex_timeout_seconds=_resolved_integer_setting(
                overrides.codex_timeout_seconds if overrides is not None else None,
                saved.codex_timeout_seconds,
                defaults.codex_timeout_seconds,
                "AGENTBRAID_CODEX_TIMEOUT_SECONDS",
            ),
            max_output_bytes=_resolved_integer_setting(
                overrides.max_output_bytes if overrides is not None else None,
                saved.max_output_bytes,
                defaults.max_output_bytes,
                "AGENTBRAID_MAX_OUTPUT_BYTES",
            ),
            worktree_dir=str(self.config.worktree_dir),
        )
        return settings

    def _settings_for_run(self, run: RunSnapshot) -> RunExecutionSettings:
        return run.execution_settings or self._resolve_execution_settings(run.request)

    def _config_for_run(self, run: RunSnapshot) -> AgentBraidConfig:
        settings = self._settings_for_run(run)
        return replace(
            self.config,
            codex_binary=settings.codex_binary,
            codex_model=settings.codex_model,
            codex_reasoning_effort=settings.codex_reasoning_effort,
            codex_timeout_seconds=settings.codex_timeout_seconds,
            max_parallel_codex=settings.max_parallel_codex,
            max_output_bytes=settings.max_output_bytes,
            max_task_attempts=settings.max_task_attempts,
            worktree_dir=Path(settings.worktree_dir),
        )

    def _codex_for_run(self, run: RunSnapshot) -> CodexProvider:
        if self._codex_override is not None:
            return self._codex_override
        return CodexAdapter(self._config_for_run(run))

    def _worktrees_for_settings(self, settings: RunExecutionSettings) -> WorktreeManager:
        if self._worktrees_override is not None:
            return self._worktrees_override
        return WorktreeManager(self.workspace, Path(settings.worktree_dir))

    def _worktrees_for_run(self, run: RunSnapshot) -> WorktreeManager:
        return self._worktrees_for_settings(self._settings_for_run(run))

    def _normalize_request(self, request: StartRunRequest) -> StartRunRequest:
        if request.workspace is not None:
            requested = Path(request.workspace).expanduser()
            if not requested.is_absolute():
                requested = self.workspace / requested
            if requested.resolve() != self.workspace:
                raise SecurityBoundaryError(
                    "run workspace must match the configured AgentBraid workspace",
                    detail=f"configured={self.workspace}, requested={requested.resolve()}",
                )
        return request.model_copy(update={"workspace": str(self.workspace)})


def _provider_usage_record(
    phase: str,
    model: str,
    invocation: StructuredProviderResult[Any],
    *,
    reasoning_effort: CodexReasoningEffort | None = None,
    task_id: str | None = None,
    attempt: int | None = None,
    outcome: ProviderInvocationOutcome | None = None,
) -> ProviderUsageRecord:
    return ProviderUsageRecord(
        phase=phase,
        executor=Executor.CODEX,
        model=model,
        reasoning_effort=reasoning_effort,
        task_id=task_id,
        attempt=attempt,
        outcome=outcome,
        input_tokens=invocation.usage.input_tokens,
        cached_input_tokens=invocation.usage.cached_input_tokens,
        output_tokens=invocation.usage.output_tokens,
        reasoning_output_tokens=invocation.usage.reasoning_output_tokens,
        duration_seconds=invocation.duration_seconds,
    )


def _resolved_integer_setting(
    override: int | None,
    saved: int,
    environment_value: int,
    environment_name: str,
) -> int:
    if environment_name in os.environ:
        return environment_value
    return override if override is not None else saved


def _fallback_display_names(goal: str) -> LocalizedRunNames:
    compact = " ".join(goal.split())[:100] or "AgentBraid run"
    return LocalizedRunNames.model_validate({"en": compact, "zh-TW": compact, "zh-CN": compact})


def _assert_not_child() -> None:
    if os.environ.get("AGENTBRAID_CHILD") == "1":
        raise SecurityBoundaryError(
            "nested AgentBraid runs are disabled",
            detail="AGENTBRAID_CHILD=1",
        )


def _integration_branch(run: RunSnapshot) -> str:
    if run.integration_branch is None:
        raise StateError(f"run has no integration branch: {run.run_id}")
    return run.integration_branch


def _task_by_id(run: RunSnapshot, task_id: str) -> TaskState:
    for task in run.tasks:
        if task.spec.task_id == task_id:
            return task
    raise StateError(f"task not found: {run.run_id}/{task_id}")


def _require_success_evidence(task: TaskSpec, result: WorkerResult) -> None:
    if not result.validations or not all(evidence.passed for evidence in result.validations):
        raise StateError(
            f"successful mutating task lacks passing validation evidence: {task.task_id}"
        )
