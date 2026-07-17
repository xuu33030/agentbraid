from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from platformdirs import user_state_path

from agentbraid.errors import ConfigurationError
from agentbraid.models import WorkspaceSettings
from agentbraid.security import is_codex_binary


def _positive_int(value: object, name: str) -> int:
    try:
        parsed = int(str(value))
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"{name} must be an integer") from exc
    if parsed <= 0:
        raise ConfigurationError(f"{name} must be positive")
    return parsed


@dataclass(frozen=True, slots=True)
class AgentBraidConfig:
    state_dir: Path
    database_path: Path
    worktree_dir: Path
    codex_binary: str = "codex"
    codex_model: str | None = None
    codex_timeout_seconds: int = 1800
    max_parallel_codex: int = 1
    max_output_bytes: int = 10 * 1024 * 1024
    max_task_attempts: int = 2

    @classmethod
    def load(cls, workspace: Path | None = None) -> AgentBraidConfig:
        state_dir = Path(
            os.environ.get("AGENTBRAID_STATE_DIR", user_state_path("agentbraid", appauthor=False))
        ).expanduser()
        config = cls(
            state_dir=state_dir,
            database_path=state_dir / "agentbraid.db",
            worktree_dir=state_dir / "worktrees",
        )

        if workspace is not None:
            config_file = workspace.resolve() / ".agentbraid.toml"
            if config_file.is_file():
                config = config._with_mapping(_load_toml(config_file))

        environment: dict[str, object] = {}
        environment_names = {
            "AGENTBRAID_DATABASE_PATH": "database_path",
            "AGENTBRAID_WORKTREE_DIR": "worktree_dir",
            "AGENTBRAID_CODEX_BINARY": "codex_binary",
            "AGENTBRAID_CODEX_MODEL": "codex_model",
            "AGENTBRAID_CODEX_TIMEOUT_SECONDS": "codex_timeout_seconds",
            "AGENTBRAID_MAX_PARALLEL_CODEX": "max_parallel_codex",
            "AGENTBRAID_MAX_OUTPUT_BYTES": "max_output_bytes",
            "AGENTBRAID_MAX_TASK_ATTEMPTS": "max_task_attempts",
        }
        for environment_name, field_name in environment_names.items():
            if environment_name in os.environ:
                environment[field_name] = os.environ[environment_name]
        return config._with_mapping(environment)

    def _with_mapping(self, values: dict[str, object]) -> AgentBraidConfig:
        supported = {
            "database_path",
            "worktree_dir",
            "codex_binary",
            "codex_model",
            "codex_timeout_seconds",
            "max_parallel_codex",
            "max_output_bytes",
            "max_task_attempts",
        }
        unknown = sorted(set(values) - supported)
        if unknown:
            raise ConfigurationError(f"unknown configuration keys: {', '.join(unknown)}")

        normalized: dict[str, Any] = {}
        for name, value in values.items():
            if name in {"database_path", "worktree_dir"}:
                normalized[name] = Path(str(value)).expanduser()
            elif name in {
                "codex_timeout_seconds",
                "max_parallel_codex",
                "max_output_bytes",
                "max_task_attempts",
            }:
                normalized[name] = _positive_int(value, name)
            elif name == "codex_model":
                normalized[name] = str(value).strip() or None
            elif name == "codex_binary":
                binary = str(value).strip()
                if not is_codex_binary(binary):
                    raise ConfigurationError("codex_binary must name the official codex executable")
                normalized[name] = binary
            else:
                normalized[name] = str(value)
        return replace(self, **normalized)

    def ensure_directories(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.worktree_dir.mkdir(parents=True, exist_ok=True)

    def default_workspace_settings(self, workspace: Path) -> WorkspaceSettings:
        return WorkspaceSettings(
            workspace=str(workspace.expanduser().resolve()),
            codex_binary=self.codex_binary,
            codex_model=self.codex_model,
            max_parallel_codex=min(self.max_parallel_codex, 8),
            max_task_attempts=min(self.max_task_attempts, 3),
            codex_timeout_seconds=min(max(self.codex_timeout_seconds, 60), 7200),
            max_output_bytes=min(max(self.max_output_bytes, 1024 * 1024), 100 * 1024 * 1024),
            worktree_dir=str(self.worktree_dir.expanduser().resolve()),
        )

    def with_workspace_runtime(self, settings: WorkspaceSettings) -> AgentBraidConfig:
        values: dict[str, object] = {}
        if "AGENTBRAID_CODEX_BINARY" not in os.environ:
            values["codex_binary"] = settings.codex_binary
        if "AGENTBRAID_WORKTREE_DIR" not in os.environ:
            values["worktree_dir"] = settings.worktree_dir
        return self._with_mapping(values)


def _load_toml(path: Path) -> dict[str, object]:
    try:
        with path.open("rb") as handle:
            payload = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigurationError(f"could not read configuration: {path}", detail=str(exc)) from exc
    section = payload.get("agentbraid", {})
    if not isinstance(section, dict):
        raise ConfigurationError("[agentbraid] must be a TOML table")
    return {str(key): value for key, value in section.items()}
