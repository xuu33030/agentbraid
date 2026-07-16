from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agentbraid.errors import RoutingError
from agentbraid.models import (
    CapabilitySnapshot,
    CapabilityStatus,
    Executor,
    RiskLevel,
    RunPlan,
    TaskKind,
    TaskSpec,
)
from agentbraid.router import TaskRouter

NOW = datetime(2026, 7, 16, tzinfo=UTC)


def task(
    *,
    kind: TaskKind = TaskKind.IMPLEMENTATION,
    preferred: Executor | None = None,
    risk: RiskLevel = RiskLevel.LOW,
    mutates_workspace: bool = False,
    required_capabilities: list[str] | None = None,
) -> TaskSpec:
    return TaskSpec(
        task_id="route-task",
        title="Route task",
        instructions="Route this bounded task.",
        kind=kind,
        preferred_executor=preferred,
        risk=risk,
        mutates_workspace=mutates_workspace,
        required_capabilities=required_capabilities or [],
        acceptance_criteria=["The route is deterministic."],
    )


def capability(
    executor: Executor,
    *,
    status: CapabilityStatus = CapabilityStatus.HEALTHY,
    successes: int = 0,
    failures: int = 0,
    latency: float = 0,
    cooldown_until: datetime | None = None,
    capabilities: str = "",
) -> CapabilitySnapshot:
    return CapabilitySnapshot(
        executor=executor,
        model=f"{executor.value}-model",
        status=status,
        successes=successes,
        failures=failures,
        average_latency_seconds=latency,
        cooldown_until=cooldown_until,
        metadata={"capabilities": capabilities} if capabilities else {},
        updated_at=NOW,
    )


def route(
    task_spec: TaskSpec,
    codex: CapabilitySnapshot | None = None,
    host: CapabilitySnapshot | None = None,
) -> tuple[Executor, str]:
    decision = TaskRouter().route_task(
        task_spec,
        {
            Executor.CODEX: codex or capability(Executor.CODEX),
            Executor.HOST: host or capability(Executor.HOST),
        },
        now=NOW,
    )
    return decision.executor, decision.rationale


def test_host_preference_routes_low_risk_research_to_host() -> None:
    executor, rationale = route(task(kind=TaskKind.RESEARCH, preferred=Executor.HOST))

    assert executor == Executor.HOST
    assert "selected=host" in rationale
    assert "fit=" in rationale
    assert "outcomes=" in rationale
    assert "latency=" in rationale


def test_unavailable_host_overrides_host_preference() -> None:
    executor, rationale = route(
        task(preferred=Executor.HOST),
        host=capability(Executor.HOST, status=CapabilityStatus.UNAVAILABLE),
    )

    assert executor == Executor.CODEX
    assert "host=-1000.00[unavailable" in rationale


def test_active_cooldown_is_unavailable() -> None:
    executor, _ = route(
        task(preferred=Executor.HOST),
        host=capability(
            Executor.HOST,
            status=CapabilityStatus.COOLDOWN,
            cooldown_until=NOW + timedelta(minutes=10),
        ),
    )

    assert executor == Executor.CODEX


def test_high_risk_mutation_prefers_codex_without_explicit_preference() -> None:
    executor, _ = route(task(risk=RiskLevel.HIGH, mutates_workspace=True))

    assert executor == Executor.CODEX


def test_required_capability_can_select_host() -> None:
    executor, rationale = route(
        task(
            kind=TaskKind.EXPLORATION,
            required_capabilities=["design-inspection"],
        ),
        host=capability(Executor.HOST, capabilities="design-inspection"),
    )

    assert executor == Executor.HOST
    assert "capabilities=0.50" in rationale


def test_explicit_executor_requirement_is_hard_constraint() -> None:
    executor, _ = route(task(required_capabilities=["executor:host"]))

    assert executor == Executor.HOST


def test_explicit_executor_requirement_does_not_fallback() -> None:
    with pytest.raises(RoutingError, match="no executor is available"):
        route(
            task(required_capabilities=["executor:host"]),
            host=capability(Executor.HOST, status=CapabilityStatus.UNAVAILABLE),
        )


def test_outcomes_and_latency_affect_score_deterministically() -> None:
    task_spec = task(kind=TaskKind.TESTING)
    codex = capability(
        Executor.CODEX,
        successes=0,
        failures=10,
        latency=450,
    )
    host = capability(Executor.HOST, successes=10, failures=0, latency=1)

    first, first_rationale = route(task_spec, codex=codex, host=host)
    second, second_rationale = route(task_spec, codex=codex, host=host)

    assert first == Executor.HOST
    assert second == first
    assert second_rationale == first_rationale


def test_no_available_executor_fails_closed() -> None:
    unavailable_codex = capability(
        Executor.CODEX,
        status=CapabilityStatus.UNAVAILABLE,
    )
    unavailable_host = capability(
        Executor.HOST,
        status=CapabilityStatus.UNAVAILABLE,
    )

    with pytest.raises(RoutingError, match="no executor is available"):
        route(task(), codex=unavailable_codex, host=unavailable_host)


def test_route_plan_uses_requested_models_and_preserves_task_ids() -> None:
    run_plan = RunPlan(
        summary="Route one task.",
        tasks=[task(preferred=Executor.HOST)],
        final_acceptance_criteria=["Task is routed."],
    )

    assignments = TaskRouter().route_plan(
        run_plan,
        [],
        codex_model="codex-model",
        host_model="host-model",
        now=NOW,
    )

    assert list(assignments) == ["route-task"]
    assert assignments["route-task"].executor == Executor.HOST
