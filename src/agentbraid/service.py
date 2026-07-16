from __future__ import annotations

import os
from pathlib import Path
from typing import Protocol

from agentbraid.config import AgentBraidConfig
from agentbraid.errors import ProviderError, SecurityBoundaryError, StateError
from agentbraid.models import (
    CapabilitySnapshot,
    CapabilityStatus,
    DeliveryMode,
    Executor,
    HostTaskResult,
    RoutingDecision,
    RunPlan,
    RunSnapshot,
    RunStatus,
    StartRunRequest,
    TaskOutcome,
    TaskState,
    utc_now,
)
from agentbraid.providers.base import StructuredProviderResult
from agentbraid.providers.codex import CodexAdapter
from agentbraid.store import StateStore


class LeadPlanner(Protocol):
    async def plan(
        self,
        request: StartRunRequest,
        workspace: Path,
    ) -> StructuredProviderResult[RunPlan]: ...


class AgentBraidService:
    """Application boundary shared by MCP transports and local commands."""

    def __init__(
        self,
        config: AgentBraidConfig,
        workspace: Path,
        *,
        store: StateStore | None = None,
        codex: LeadPlanner | None = None,
    ) -> None:
        self.config = config
        self.workspace = workspace.expanduser().resolve()
        self.store = store or StateStore(config.database_path)
        self.codex = codex or CodexAdapter(config)

    @classmethod
    def from_workspace(cls, workspace: Path) -> AgentBraidService:
        resolved = workspace.expanduser().resolve()
        config = AgentBraidConfig.load(resolved)
        config.ensure_directories()
        return cls(config, resolved)

    async def start_run(self, request: StartRunRequest) -> RunSnapshot:
        _assert_not_child()
        normalized_request = self._normalize_request(request)
        run = self.store.create_run(normalized_request)
        self.store.begin_planning(run.run_id)
        model = self.config.codex_model or "codex-default"
        try:
            planning = await self.codex.plan(normalized_request, self.workspace)
            assignments = {
                task.task_id: _initial_assignment(task.preferred_executor)
                for task in planning.output.tasks
            }
            integration_branch = (
                f"agentbraid/{run.run_id[:12]}"
                if normalized_request.delivery_mode == DeliveryMode.INTEGRATION_BRANCH
                else None
            )
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
        self.store.record_capability_result(
            Executor.CODEX,
            model,
            succeeded=True,
            latency_seconds=planning.duration_seconds,
            status=CapabilityStatus.HEALTHY,
        )
        return snapshot

    def claim_host_task(
        self,
        run_id: str,
        host_id: str,
        *,
        task_id: str | None = None,
    ) -> TaskState | None:
        claimed = self.store.claim_host_task(run_id, host_id, task_id=task_id)
        if claimed is not None:
            self._touch_host_capability(run_id, host_id)
        return claimed

    def submit_host_result(
        self,
        run_id: str,
        task_id: str,
        host_id: str,
        result: HostTaskResult,
    ) -> TaskState:
        completed = self.store.submit_task_result(
            run_id,
            task_id,
            result,
            claimed_by=host_id,
            commit_sha=result.commit_sha,
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
        return completed

    def get_run(self, run_id: str) -> RunSnapshot:
        return self.store.get_run(run_id)

    def cancel_run(self, run_id: str) -> RunSnapshot:
        return self.store.cancel_run(run_id)

    def list_capabilities(self) -> list[CapabilitySnapshot]:
        return self.store.list_capabilities()

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


def _initial_assignment(preferred: Executor | None) -> RoutingDecision:
    executor = preferred or Executor.CODEX
    rationale = (
        f"The lead explicitly preferred {executor.value}."
        if preferred is not None
        else "No executor preference was supplied; Codex is the conservative default."
    )
    return RoutingDecision(executor=executor, score=1.0 if preferred else 0.5, rationale=rationale)


def _assert_not_child() -> None:
    if os.environ.get("AGENTBRAID_CHILD") == "1":
        raise SecurityBoundaryError(
            "nested AgentBraid runs are disabled",
            detail="AGENTBRAID_CHILD=1",
        )
