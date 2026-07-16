from __future__ import annotations


class AgentBraidError(Exception):
    """Base exception with a stable machine-readable code."""

    code = "agentbraid_error"

    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail

    def as_dict(self) -> dict[str, str]:
        payload = {"code": self.code, "message": self.message}
        if self.detail:
            payload["detail"] = self.detail
        return payload


class ConfigurationError(AgentBraidError):
    code = "configuration_error"


class StateError(AgentBraidError):
    code = "state_error"


class RunNotFoundError(StateError):
    code = "run_not_found"


class TaskNotFoundError(StateError):
    code = "task_not_found"


class InvalidTransitionError(StateError):
    code = "invalid_transition"


class ProviderError(AgentBraidError):
    code = "provider_error"

    def __init__(
        self,
        message: str,
        *,
        detail: str | None = None,
        retryable: bool = False,
        quota_limited: bool = False,
    ) -> None:
        super().__init__(message, detail=detail)
        self.retryable = retryable
        self.quota_limited = quota_limited


class ProviderUnavailableError(ProviderError):
    code = "provider_unavailable"


class ProviderOutputError(ProviderError):
    code = "provider_output_error"


class SecurityBoundaryError(AgentBraidError):
    code = "security_boundary_error"


class WorktreeError(AgentBraidError):
    code = "worktree_error"
