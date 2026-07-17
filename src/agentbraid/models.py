from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def utc_now() -> datetime:
    return datetime.now(UTC)


TaskId = Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{0,62}$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Executor(StrEnum):
    CODEX = "codex"
    HOST = "host"


class TaskKind(StrEnum):
    EXPLORATION = "exploration"
    PLANNING = "planning"
    IMPLEMENTATION = "implementation"
    TESTING = "testing"
    REVIEW = "review"
    RESEARCH = "research"
    DOCUMENTATION = "documentation"
    INTEGRATION = "integration"


class RiskLevel(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class RunStatus(StrEnum):
    CREATED = "created"
    PLANNING = "planning"
    RUNNING = "running"
    INTEGRATING = "integrating"
    REVIEWING = "reviewing"
    COMPLETED = "completed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    FAILED = "failed"


class TaskStatus(StrEnum):
    PENDING = "pending"
    READY = "ready"
    RUNNING = "running"
    RETRYING = "retrying"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class TaskOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"


class ProviderInvocationOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    APPROVED = "approved"
    REJECTED = "rejected"


class DeliveryMode(StrEnum):
    INTEGRATION_BRANCH = "integration_branch"
    REPORT_ONLY = "report_only"


class ValidationEvidence(StrictModel):
    command: str = Field(min_length=1, max_length=1000)
    passed: bool
    output: str = Field(default="", max_length=20_000)


class TaskSpec(StrictModel):
    task_id: TaskId
    title: str = Field(min_length=1, max_length=200)
    instructions: str = Field(min_length=1, max_length=20_000)
    kind: TaskKind
    preferred_executor: Executor | None = None
    mutates_workspace: bool = False
    dependencies: list[TaskId] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(min_length=1, max_length=20)
    required_capabilities: list[str] = Field(default_factory=list, max_length=20)
    risk: RiskLevel = RiskLevel.LOW
    complexity: int = Field(default=2, ge=1, le=5)
    max_attempts: int = Field(default=2, ge=1, le=3)

    @model_validator(mode="after")
    def validate_dependencies(self) -> TaskSpec:
        if self.task_id in self.dependencies:
            raise ValueError("a task cannot depend on itself")
        if len(self.dependencies) != len(set(self.dependencies)):
            raise ValueError("task dependencies must be unique")
        return self


class RunPlan(StrictModel):
    schema_version: Literal["1.0"] = "1.0"
    summary: str = Field(min_length=1, max_length=5000)
    tasks: list[TaskSpec] = Field(min_length=1, max_length=100)
    final_acceptance_criteria: list[str] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def validate_graph(self) -> RunPlan:
        task_ids = [task.task_id for task in self.tasks]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("task IDs must be unique")
        known = set(task_ids)
        for task in self.tasks:
            missing = set(task.dependencies) - known
            if missing:
                raise ValueError(
                    f"task {task.task_id} has unknown dependencies: {', '.join(sorted(missing))}"
                )
        self._topological_ids()
        return self

    def topological_ids(self) -> list[str]:
        return self._topological_ids()

    def _topological_ids(self) -> list[str]:
        dependencies = {task.task_id: set(task.dependencies) for task in self.tasks}
        ordered: list[str] = []
        ready = sorted(task_id for task_id, deps in dependencies.items() if not deps)
        while ready:
            task_id = ready.pop(0)
            ordered.append(task_id)
            for candidate in sorted(dependencies):
                if task_id in dependencies[candidate]:
                    dependencies[candidate].remove(task_id)
                    newly_ready = (
                        not dependencies[candidate]
                        and candidate not in ordered
                        and candidate not in ready
                    )
                    if newly_ready:
                        ready.append(candidate)
                        ready.sort()
        if len(ordered) != len(dependencies):
            raise ValueError("task dependency graph contains a cycle")
        return ordered


class WorkerResult(StrictModel):
    outcome: TaskOutcome
    summary: str = Field(min_length=1, max_length=10_000)
    changed_files: list[str] = Field(default_factory=list, max_length=500)
    validations: list[ValidationEvidence] = Field(default_factory=list, max_length=50)
    notes: list[str] = Field(default_factory=list, max_length=50)
    confidence: float = Field(default=0.5, ge=0, le=1)
    error: str | None = Field(default=None, max_length=20_000)


class HostTaskResult(WorkerResult):
    commit_sha: str | None = Field(default=None, pattern=r"^[0-9a-f]{40,64}$")
    artifacts: list[str] = Field(default_factory=list, max_length=100)


class RoutingDecision(StrictModel):
    executor: Executor
    score: float
    rationale: str = Field(min_length=1, max_length=2000)


class StartRunRequest(StrictModel):
    goal: str = Field(min_length=1, max_length=50_000)
    workspace: str | None = None
    host_model: str = Field(default="antigravity-auto", min_length=1, max_length=200)
    constraints: list[str] = Field(default_factory=list, max_length=50)
    delivery_mode: DeliveryMode = DeliveryMode.INTEGRATION_BRANCH


class TaskState(StrictModel):
    run_id: str
    spec: TaskSpec
    status: TaskStatus
    executor: Executor
    assignment_rationale: str
    attempt: int = 0
    claimed_by: str | None = None
    result: HostTaskResult | WorkerResult | None = None
    worktree_path: str | None = None
    commit_sha: str | None = None
    error: str | None = None
    created_at: datetime
    updated_at: datetime


class ProviderUsageRecord(StrictModel):
    phase: str = Field(min_length=1, max_length=50)
    executor: Executor
    model: str = Field(min_length=1, max_length=200)
    task_id: TaskId | None = None
    attempt: int | None = Field(default=None, ge=1)
    outcome: ProviderInvocationOutcome | None = None
    input_tokens: int = Field(default=0, ge=0)
    cached_input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    reasoning_output_tokens: int = Field(default=0, ge=0)
    duration_seconds: float = Field(default=0, ge=0)
    created_at: datetime = Field(default_factory=utc_now)


class RunSnapshot(StrictModel):
    run_id: str
    request: StartRunRequest
    status: RunStatus
    plan: RunPlan | None = None
    lead_thread_id: str | None = None
    integration_branch: str | None = None
    base_branch: str | None = None
    base_commit: str | None = None
    final_summary: str | None = None
    error: str | None = None
    tasks: list[TaskState] = Field(default_factory=list)
    provider_usage: list[ProviderUsageRecord] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class RunSummary(StrictModel):
    run_id: str
    workspace: str
    goal: str
    status: RunStatus
    delivery_mode: DeliveryMode
    base_branch: str | None = None
    task_count: int = Field(default=0, ge=0)
    succeeded_task_count: int = Field(default=0, ge=0)
    failed_task_count: int = Field(default=0, ge=0)
    observed_total_tokens: int = Field(default=0, ge=0)
    retry_tokens: int = Field(default=0, ge=0)
    created_at: datetime
    updated_at: datetime


class WorkspaceSummary(StrictModel):
    workspace: str
    run_count: int = Field(default=0, ge=0)
    active_run_count: int = Field(default=0, ge=0)
    observed_total_tokens: int = Field(default=0, ge=0)
    updated_at: datetime


class RunEvent(StrictModel):
    event_id: int = Field(ge=1)
    run_id: str
    task_id: TaskId | None = None
    event_type: str = Field(min_length=1, max_length=100)
    payload: dict[str, object] = Field(default_factory=dict)
    created_at: datetime


class ApplyReadiness(StrictModel):
    can_apply: bool
    blockers: list[str] = Field(default_factory=list)
    expected_branch: str | None = None
    expected_commit: str | None = None
    current_branch: str | None = None
    current_commit: str | None = None


class CapabilityStatus(StrEnum):
    HEALTHY = "healthy"
    CONSTRAINED = "constrained"
    COOLDOWN = "cooldown"
    UNAVAILABLE = "unavailable"


class ReviewSeverity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"


class ReviewFinding(StrictModel):
    severity: ReviewSeverity
    title: str = Field(min_length=1, max_length=200)
    detail: str = Field(min_length=1, max_length=5000)
    task_id: TaskId | None = None


class RunReview(StrictModel):
    approved: bool
    summary: str = Field(min_length=1, max_length=10_000)
    findings: list[ReviewFinding] = Field(default_factory=list, max_length=100)
    validations: list[ValidationEvidence] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def validate_approval(self) -> RunReview:
        has_error = any(finding.severity == ReviewSeverity.ERROR for finding in self.findings)
        if self.approved and has_error:
            raise ValueError("an approved review cannot contain error findings")
        return self


class CapabilitySnapshot(StrictModel):
    executor: Executor
    model: str
    status: CapabilityStatus = CapabilityStatus.HEALTHY
    successes: int = 0
    failures: int = 0
    average_latency_seconds: float = 0
    cooldown_until: datetime | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    updated_at: datetime = Field(default_factory=utc_now)


class ApplyRunResult(StrictModel):
    run_id: str
    integration_branch: str
    commit_sha: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    applied_at: datetime = Field(default_factory=utc_now)
