from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

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
    ApplyRunResult,
    CapabilitySnapshot,
    CapabilityStatus,
    DeliveryMode,
    Executor,
    HostTaskResult,
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
    utc_now,
)
from agentbraid.providers.base import StructuredProviderResult
from agentbraid.providers.codex import CodexAdapter
from agentbraid.router import TaskRouter
from agentbraid.store import StateStore
from agentbraid.worktrees import WorktreeManager


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
        self.store = store or StateStore(config.database_path)
        self.codex = codex or CodexAdapter(config)
        self.router = router or TaskRouter()
        self.worktrees = worktrees or WorktreeManager(self.workspace, config.worktree_dir)

    @classmethod
    def from_workspace(cls, workspace: Path) -> AgentBraidService:
        resolved = workspace.expanduser().resolve()
        config = AgentBraidConfig.load(resolved)
        config.ensure_directories()
        return cls(config, resolved)

    async def start_run(self, request: StartRunRequest) -> RunSnapshot:
        _assert_not_child()
        self.worktrees.assert_clean_workspace()
        normalized_request = self._normalize_request(request)
        run = self.store.create_run(normalized_request)
        self.store.begin_planning(run.run_id)
        model = self.config.codex_model or "codex-default"
        try:
            planning = await self.codex.plan(normalized_request, self.workspace)
            self.store.record_capability_result(
                Executor.CODEX,
                model,
                succeeded=True,
                latency_seconds=planning.duration_seconds,
                status=CapabilityStatus.HEALTHY,
            )
            assignments = self.router.route_plan(
                planning.output,
                self.store.list_capabilities(),
                codex_model=model,
                host_model=normalized_request.host_model,
            )
            integration_branch = f"agentbraid/integration/{run.run_id[:12]}"
            self.worktrees.prepare_run(run.run_id, integration_branch)
            snapshot = self.store.save_plan(
                run.run_id,
                planning.output,
                assignments,
                lead_thread_id=planning.thread_id,
                integration_branch=integration_branch,
            )
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
        claimed = self.store.claim_host_task(run_id, host_id, task_id=task_id)
        if claimed is not None:
            run = self.store.get_run(run_id)
            integration_branch = _integration_branch(run)
            try:
                if claimed.spec.mutates_workspace:
                    worktree = self.worktrees.prepare_task(
                        run_id,
                        claimed.spec.task_id,
                        integration_branch,
                    )
                else:
                    worktree = self.worktrees.prepare_run(run_id, integration_branch)
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
                    claimed_by=host_id,
                )
                raise
            self._touch_host_capability(run_id, host_id)
        return claimed

    async def submit_host_result(
        self,
        run_id: str,
        task_id: str,
        host_id: str,
        result: HostTaskResult,
    ) -> TaskState:
        task = _task_by_id(self.store.get_run(run_id), task_id)
        if task.status != TaskStatus.RUNNING or task.executor != Executor.HOST:
            raise StateError(f"host task is not running: {run_id}/{task_id}")
        if task.claimed_by != host_id:
            raise StateError(f"host task is claimed by another worker: {run_id}/{task_id}")
        commit_sha = result.commit_sha
        if result.outcome == TaskOutcome.SUCCEEDED and task.spec.mutates_workspace:
            _require_success_evidence(task.spec, result)
            if commit_sha is None:
                raise StateError("successful mutating host tasks must provide a commit SHA")
            try:
                self.worktrees.integrate_task(
                    run_id,
                    task_id,
                    _integration_branch(self.store.get_run(run_id)),
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
        completed = self.store.submit_task_result(
            run_id,
            task_id,
            result,
            claimed_by=host_id,
            worktree_path=task.worktree_path,
            commit_sha=commit_sha,
        )
        self.store.record_capability_result(
            Executor.HOST,
            self.store.get_run(run_id).request.host_model,
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
        return self.store.cancel_run(run_id)

    def list_capabilities(self) -> list[CapabilitySnapshot]:
        return self.store.list_capabilities()

    def apply_run(self, run_id: str) -> ApplyRunResult:
        run = self.store.get_run(run_id)
        if run.request.delivery_mode != DeliveryMode.INTEGRATION_BRANCH:
            raise StateError("report-only runs cannot be applied to the primary workspace")
        if run.status != RunStatus.COMPLETED:
            raise StateError(f"run must complete final review before apply: {run.status.value}")
        integration_branch = _integration_branch(run)
        commit_sha = self.worktrees.apply_integration(integration_branch)
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

    async def _drain_codex_tasks(self, run_id: str) -> None:
        model = self.config.codex_model or "codex-default"
        while self.store.get_run(run_id).status == RunStatus.RUNNING:
            task = self.store.claim_task(run_id, Executor.CODEX, "agentbraid-codex-worker")
            if task is None:
                return
            run = self.store.get_run(run_id)
            integration_branch = _integration_branch(run)
            worktree_path = Path(task.worktree_path) if task.worktree_path else None
            commit_sha: str | None = None
            try:
                if task.spec.mutates_workspace:
                    worktree = self.worktrees.prepare_task(
                        run_id,
                        task.spec.task_id,
                        integration_branch,
                    )
                else:
                    worktree = self.worktrees.prepare_run(run_id, integration_branch)
                worktree_path = worktree.path
                task = self.store.set_task_worktree(run_id, task.spec.task_id, worktree.path)
                invocation = await self.codex.execute_task(
                    task.spec,
                    worktree.path,
                    run_id=run_id,
                )
                result = invocation.output
                if result.outcome == TaskOutcome.SUCCEEDED and task.spec.mutates_workspace:
                    _require_success_evidence(task.spec, result)
                    commit_sha = self.worktrees.commit_task(
                        worktree.path,
                        task.spec.task_id,
                        task.spec.title,
                    )
                    self.worktrees.integrate_task(
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
            self.store.submit_task_result(
                run_id,
                task.spec.task_id,
                result,
                claimed_by="agentbraid-codex-worker",
                worktree_path=str(worktree_path) if worktree_path is not None else None,
                commit_sha=commit_sha,
            )

    async def _finalize_if_ready(self, run_id: str) -> None:
        run = self.store.get_run(run_id)
        if run.status not in {RunStatus.INTEGRATING, RunStatus.REVIEWING}:
            return
        try:
            integration = self.worktrees.prepare_run(run_id, _integration_branch(run))
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
        model = self.config.codex_model or "codex-default"
        try:
            invocation = await self.codex.review_run(reviewing, integration.path)
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

    def _touch_host_capability(self, run_id: str, host_id: str) -> None:
        model = self.store.get_run(run_id).request.host_model
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
