from __future__ import annotations

from pathlib import Path

import pytest

from agentbraid.config import AgentBraidConfig
from agentbraid.errors import SecurityBoundaryError
from agentbraid.models import (
    Executor,
    HostTaskResult,
    RunPlan,
    RunStatus,
    StartRunRequest,
    TaskKind,
    TaskOutcome,
    TaskSpec,
    TaskStatus,
)
from agentbraid.providers.base import ProviderUsage, StructuredProviderResult
from agentbraid.service import AgentBraidService
from agentbraid.store import StateStore


class FakePlanner:
    def __init__(self, plan: RunPlan) -> None:
        self.output_plan = plan
        self.requests: list[StartRunRequest] = []

    async def plan(
        self,
        request: StartRunRequest,
        workspace: Path,
    ) -> StructuredProviderResult[RunPlan]:
        self.requests.append(request)
        return StructuredProviderResult(
            output=self.output_plan,
            thread_id="lead-thread",
            usage=ProviderUsage(input_tokens=100, output_tokens=20),
            events=(),
            duration_seconds=1.5,
        )


def make_config(tmp_path: Path) -> AgentBraidConfig:
    state_dir = tmp_path / "state"
    return AgentBraidConfig(
        state_dir=state_dir,
        database_path=state_dir / "agentbraid.db",
        worktree_dir=state_dir / "worktrees",
        codex_model="gpt-test",
    )


def host_plan(*, mutates_workspace: bool = False) -> RunPlan:
    return RunPlan(
        summary="Delegate one bounded task to the active host.",
        tasks=[
            TaskSpec(
                task_id="host-task",
                title="Complete host task",
                instructions="Complete and validate the bounded host task.",
                kind=TaskKind.IMPLEMENTATION,
                preferred_executor=Executor.HOST,
                mutates_workspace=mutates_workspace,
                acceptance_criteria=["The task is validated."],
            )
        ],
        final_acceptance_criteria=["The host task succeeds."],
    )


@pytest.mark.asyncio
async def test_service_starts_run_and_tracks_host_capability(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    store = StateStore(config.database_path)
    planner = FakePlanner(host_plan())
    service = AgentBraidService(config, tmp_path, store=store, codex=planner)

    run = await service.start_run(
        StartRunRequest(goal="Complete the feature.", host_model="antigravity-test")
    )
    claimed = service.claim_host_task(run.run_id, "host-session")

    assert run.status == RunStatus.RUNNING
    assert run.request.workspace == str(tmp_path)
    assert run.lead_thread_id == "lead-thread"
    assert run.tasks[0].status == TaskStatus.READY
    assert claimed is not None
    assert claimed.status == TaskStatus.RUNNING
    capabilities = service.list_capabilities()
    assert [(item.executor, item.model) for item in capabilities] == [
        (Executor.CODEX, "gpt-test"),
        (Executor.HOST, "antigravity-test"),
    ]


@pytest.mark.asyncio
async def test_service_rejects_workspace_escape(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    service = AgentBraidService(config, tmp_path, codex=FakePlanner(host_plan()))

    with pytest.raises(SecurityBoundaryError, match="must match"):
        await service.start_run(StartRunRequest(goal="Escape.", workspace=str(tmp_path.parent)))


@pytest.mark.asyncio
async def test_service_rejects_nested_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("AGENTBRAID_CHILD", "1")
    config = make_config(tmp_path)
    service = AgentBraidService(config, tmp_path, codex=FakePlanner(host_plan()))

    with pytest.raises(SecurityBoundaryError, match="nested"):
        await service.start_run(StartRunRequest(goal="Do not recurse."))


@pytest.mark.asyncio
async def test_service_submits_host_commit(tmp_path: Path) -> None:
    config = make_config(tmp_path)
    service = AgentBraidService(
        config, tmp_path, codex=FakePlanner(host_plan(mutates_workspace=True))
    )
    run = await service.start_run(StartRunRequest(goal="Edit and commit."))
    assert service.claim_host_task(run.run_id, "host-session") is not None

    completed = service.submit_host_result(
        run.run_id,
        "host-task",
        "host-session",
        HostTaskResult(
            outcome=TaskOutcome.SUCCEEDED,
            summary="Change validated and committed.",
            commit_sha="b" * 40,
        ),
    )

    assert completed.status == TaskStatus.SUCCEEDED
    assert completed.commit_sha == "b" * 40
    assert service.get_run(run.run_id).status == RunStatus.INTEGRATING
    host_capability = next(
        item for item in service.list_capabilities() if item.executor == Executor.HOST
    )
    assert host_capability.successes == 1
