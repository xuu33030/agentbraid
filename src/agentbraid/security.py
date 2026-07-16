from __future__ import annotations

from pathlib import Path

from agentbraid.errors import SecurityBoundaryError
from agentbraid.redaction import is_sensitive_environment_name

_CODEX_BINARIES = frozenset({"codex", "codex.exe"})
_SAFE_AUTH_ENVIRONMENT: frozenset[str] = frozenset()


def assert_codex_binary(binary: str) -> None:
    if Path(binary).name.casefold() not in _CODEX_BINARIES:
        raise SecurityBoundaryError(
            "Codex adapter may only launch the official codex executable",
            detail=Path(binary).name,
        )


def is_codex_binary(binary: str) -> bool:
    return Path(binary).name.casefold() in _CODEX_BINARIES


def sanitized_provider_environment(environment: dict[str, str]) -> dict[str, str]:
    sanitized = {
        name: value
        for name, value in environment.items()
        if not is_sensitive_environment_name(name) or name in _SAFE_AUTH_ENVIRONMENT
    }
    sanitized["AGENTBRAID_CHILD"] = "1"
    sanitized["NO_COLOR"] = "1"
    return sanitized


def assert_safe_runtime_paths(
    workspace: Path,
    state_dir: Path,
    database_path: Path,
    worktree_dir: Path,
) -> None:
    resolved_workspace = workspace.expanduser().resolve()
    resolved_paths = {
        "state directory": state_dir.expanduser().resolve(),
        "database": database_path.expanduser().resolve(),
        "worktree directory": worktree_dir.expanduser().resolve(),
    }
    _assert_not_sensitive(resolved_workspace, "workspace")
    for label, path in resolved_paths.items():
        _assert_not_sensitive(path, label)
        if path == resolved_workspace or path.is_relative_to(resolved_workspace):
            raise SecurityBoundaryError(
                f"{label} must live outside the workspace",
                detail=f"workspace={resolved_workspace}, path={path}",
            )


def assert_safe_workspace(workspace: Path) -> None:
    _assert_not_sensitive(workspace.expanduser().resolve(), "workspace")


def _assert_not_sensitive(path: Path, label: str) -> None:
    home = Path.home().resolve()
    sensitive_roots = (
        home / ".aws",
        home / ".codex",
        home / ".gemini",
        home / ".gnupg",
        home / ".ssh",
        home / ".config" / "gcloud",
    )
    for sensitive_root in sensitive_roots:
        if path == sensitive_root or path.is_relative_to(sensitive_root):
            raise SecurityBoundaryError(
                f"{label} cannot target a credential-bearing directory",
                detail=str(path),
            )
