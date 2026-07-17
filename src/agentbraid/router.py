from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from agentbraid.errors import RoutingError
from agentbraid.models import (
    CapabilitySnapshot,
    CapabilityStatus,
    Executor,
    RiskLevel,
    RoutingDecision,
    RunPlan,
    TaskKind,
    TaskSpec,
    utc_now,
)

_UNAVAILABLE_SCORE = -1_000.0

_KIND_FIT: dict[TaskKind, dict[Executor, float]] = {
    TaskKind.EXPLORATION: {Executor.CODEX: 0.7, Executor.HOST: 1.0},
    TaskKind.PLANNING: {Executor.CODEX: 1.5, Executor.HOST: 0.4},
    TaskKind.IMPLEMENTATION: {Executor.CODEX: 1.3, Executor.HOST: 0.9},
    TaskKind.TESTING: {Executor.CODEX: 1.2, Executor.HOST: 0.8},
    TaskKind.REVIEW: {Executor.CODEX: 1.5, Executor.HOST: 0.3},
    TaskKind.RESEARCH: {Executor.CODEX: 0.7, Executor.HOST: 1.3},
    TaskKind.DOCUMENTATION: {Executor.CODEX: 0.8, Executor.HOST: 1.1},
    TaskKind.INTEGRATION: {Executor.CODEX: 2.0, Executor.HOST: -1.0},
}

_RISK_SCORE: dict[RiskLevel, dict[Executor, float]] = {
    RiskLevel.LOW: {Executor.CODEX: 0.0, Executor.HOST: 0.0},
    RiskLevel.MEDIUM: {Executor.CODEX: 0.3, Executor.HOST: -0.1},
    RiskLevel.HIGH: {Executor.CODEX: 0.8, Executor.HOST: -0.8},
    RiskLevel.CRITICAL: {Executor.CODEX: 1.2, Executor.HOST: -2.0},
}

_BUILTIN_CAPABILITIES: dict[Executor, frozenset[str]] = {
    Executor.CODEX: frozenset({"code", "git", "repository", "review", "shell", "testing"}),
    Executor.HOST: frozenset(
        {
            "browser",
            "code",
            "git",
            "interactive",
            "multimodal",
            "repository",
            "review",
            "shell",
            "testing",
            "web",
        }
    ),
}


@dataclass(frozen=True, slots=True)
class _ScoredExecutor:
    executor: Executor
    score: float
    components: tuple[tuple[str, float], ...]
    available: bool


class TaskRouter:
    """Deterministic v0.1 routing policy with explainable fixed weights."""

    def route_plan(
        self,
        plan: RunPlan,
        capabilities: list[CapabilitySnapshot],
        *,
        codex_model: str,
        host_model: str,
        now: datetime | None = None,
    ) -> dict[str, RoutingDecision]:
        routed_at = now or utc_now()
        selected_capabilities = {
            Executor.CODEX: _select_capability(
                capabilities,
                Executor.CODEX,
                codex_model,
            ),
            Executor.HOST: _select_capability(
                capabilities,
                Executor.HOST,
                host_model,
            ),
        }
        return {
            task.task_id: self.route_task(task, selected_capabilities, now=routed_at)
            for task in plan.tasks
        }

    def route_task(
        self,
        task: TaskSpec,
        capabilities: dict[Executor, CapabilitySnapshot],
        *,
        now: datetime | None = None,
    ) -> RoutingDecision:
        routed_at = now or utc_now()
        scores = [
            self._score(task, executor, capabilities[executor], routed_at)
            for executor in (Executor.CODEX, Executor.HOST)
        ]
        available = [score for score in scores if score.available]
        if not available:
            raise RoutingError(
                f"no executor is available for task: {task.task_id}",
                detail="; ".join(_score_summary(score) for score in scores),
            )
        selected = max(
            available,
            key=lambda item: (item.score, item.executor == Executor.CODEX),
        )
        rationale = (
            f"selected={selected.executor.value}; "
            + "; ".join(_score_summary(score) for score in scores)
            + "; ties prefer codex for lead accountability"
        )
        return RoutingDecision(
            executor=selected.executor,
            score=round(selected.score, 4),
            rationale=rationale,
        )

    def _score(
        self,
        task: TaskSpec,
        executor: Executor,
        capability: CapabilitySnapshot,
        now: datetime,
    ) -> _ScoredExecutor:
        status_score, available = _availability_score(capability, now)
        available = available and _executor_allowed(task, executor)
        components: list[tuple[str, float]] = [
            ("fit", _KIND_FIT[task.kind][executor]),
            (
                "preference",
                2.0 if task.preferred_executor == executor else 0.0,
            ),
            ("risk", _RISK_SCORE[task.risk][executor]),
            ("availability", status_score),
            ("outcomes", _outcome_score(capability)),
            ("latency", _latency_score(capability)),
            ("capabilities", _required_capability_score(task, executor, capability)),
            (
                "mutation",
                (0.8 if executor == Executor.CODEX else -0.4) if task.mutates_workspace else 0.0,
            ),
        ]
        if not available:
            return _ScoredExecutor(executor, _UNAVAILABLE_SCORE, tuple(components), False)
        return _ScoredExecutor(
            executor,
            sum(value for _, value in components),
            tuple(components),
            True,
        )


def _select_capability(
    capabilities: list[CapabilitySnapshot],
    executor: Executor,
    model: str,
) -> CapabilitySnapshot:
    exact = [
        capability
        for capability in capabilities
        if capability.executor == executor and capability.model == model
    ]
    if exact:
        return max(exact, key=lambda capability: _aware(capability.updated_at))
    return CapabilitySnapshot(executor=executor, model=model)


def _availability_score(
    capability: CapabilitySnapshot,
    now: datetime,
) -> tuple[float, bool]:
    if capability.status == CapabilityStatus.UNAVAILABLE:
        return _UNAVAILABLE_SCORE, False
    if (
        capability.status == CapabilityStatus.COOLDOWN
        and capability.cooldown_until is not None
        and _aware(capability.cooldown_until) > _aware(now)
    ):
        return _UNAVAILABLE_SCORE, False
    if capability.status == CapabilityStatus.COOLDOWN:
        return -2.0, True
    if capability.status == CapabilityStatus.CONSTRAINED:
        return -1.0, True
    return 0.8, True


def _outcome_score(capability: CapabilitySnapshot) -> float:
    attempts = capability.successes + capability.failures
    success_rate = (capability.successes + 1) / (attempts + 2)
    return (success_rate - 0.5) * 2


def _latency_score(capability: CapabilitySnapshot) -> float:
    return -min(capability.average_latency_seconds / 300, 1.5)


def _required_capability_score(
    task: TaskSpec,
    executor: Executor,
    capability: CapabilitySnapshot,
) -> float:
    declared = {
        item.strip().casefold()
        for item in capability.metadata.get("capabilities", "").split(",")
        if item.strip()
    }
    available = _BUILTIN_CAPABILITIES[executor] | declared
    score = 0.0
    for requirement in task.required_capabilities:
        normalized = requirement.strip().casefold()
        if normalized.startswith("executor:"):
            score += 1.0 if normalized == f"executor:{executor.value}" else _UNAVAILABLE_SCORE
        else:
            score += 0.5 if normalized in available else -1.5
    return score


def _executor_allowed(task: TaskSpec, executor: Executor) -> bool:
    constraints = {
        requirement.partition(":")[2].strip().casefold()
        for requirement in task.required_capabilities
        if requirement.strip().casefold().startswith("executor:")
    }
    return not constraints or executor.value in constraints


def _score_summary(score: _ScoredExecutor) -> str:
    components = ",".join(f"{name}={value:.2f}" for name, value in score.components)
    availability = "available" if score.available else "unavailable"
    return f"{score.executor.value}={score.score:.2f}[{availability};{components}]"


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
