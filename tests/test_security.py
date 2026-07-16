from __future__ import annotations

from pathlib import Path

import pytest

from agentbraid.config import AgentBraidConfig
from agentbraid.errors import AgentBraidError, SecurityBoundaryError
from agentbraid.redaction import redact_text, redact_value
from agentbraid.security import (
    assert_codex_binary,
    assert_safe_runtime_paths,
    assert_safe_workspace,
    sanitized_provider_environment,
)


def test_redaction_covers_common_secret_shapes() -> None:
    source = (
        "token=plain-secret Bearer abcdefghijklmnop "
        "https://user:password@example.test/path "
        "ghp_abcdefghijklmnopqrstuvwxyz123456"
    )

    redacted = redact_text(source)

    assert "plain-secret" not in redacted
    assert "abcdefghijklmnop" not in redacted
    assert "user:password" not in redacted
    assert "ghp_" not in redacted
    assert redacted.count("[REDACTED]") >= 4


def test_recursive_redaction_uses_sensitive_keys_without_hiding_usage() -> None:
    payload = redact_value(
        {
            "GITHUB_TOKEN": "secret-value",
            "nested": {"password": "another-secret"},
            "input_tokens": 123,
        }
    )

    assert payload["GITHUB_TOKEN"] == "[REDACTED]"
    assert payload["nested"]["password"] == "[REDACTED]"
    assert payload["input_tokens"] == 123


def test_provider_environment_removes_credentials() -> None:
    sanitized = sanitized_provider_environment(
        {
            "PATH": "/usr/bin",
            "HOME": "/home/test",
            "GOOGLE_APPLICATION_CREDENTIALS": "/secret/google.json",
            "AWS_ACCESS_KEY_ID": "access-key",
            "SSH_AUTH_SOCK": "/tmp/agent.sock",
            "CODEX_API_KEY": "codex-secret",
        }
    )

    assert sanitized == {
        "PATH": "/usr/bin",
        "HOME": "/home/test",
        "AGENTBRAID_CHILD": "1",
        "NO_COLOR": "1",
    }


def test_only_official_codex_binary_is_allowed() -> None:
    assert_codex_binary("/usr/local/bin/codex")

    with pytest.raises(SecurityBoundaryError, match="official codex"):
        assert_codex_binary("agy")


def test_runtime_state_must_live_outside_workspace(tmp_path: Path) -> None:
    with pytest.raises(SecurityBoundaryError, match="outside the workspace"):
        assert_safe_runtime_paths(
            tmp_path,
            tmp_path / "state",
            tmp_path / "state" / "agentbraid.db",
            tmp_path / "state" / "worktrees",
        )


def test_credential_directories_cannot_be_workspaces() -> None:
    with pytest.raises(SecurityBoundaryError, match="credential-bearing"):
        assert_safe_workspace(Path.home() / ".ssh" / "project")


def test_error_details_are_redacted() -> None:
    error = AgentBraidError("request failed", detail="authorization=super-secret")

    assert error.detail == "authorization=[REDACTED]"
    assert "super-secret" not in str(error.as_dict())


def test_config_dataclass_can_describe_safe_external_state(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    state = tmp_path / "state"
    config = AgentBraidConfig(
        state_dir=state,
        database_path=state / "agentbraid.db",
        worktree_dir=state / "worktrees",
    )

    assert_safe_runtime_paths(
        workspace,
        config.state_dir,
        config.database_path,
        config.worktree_dir,
    )
