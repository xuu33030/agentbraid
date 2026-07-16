from __future__ import annotations

from pathlib import Path

import pytest

from agentbraid.config import AgentBraidConfig
from agentbraid.errors import ConfigurationError


def test_config_uses_state_directory_environment_override(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    monkeypatch.setenv("AGENTBRAID_STATE_DIR", str(state_dir))

    config = AgentBraidConfig.load()
    config.ensure_directories()

    assert config.state_dir == state_dir
    assert config.database_path == state_dir / "agentbraid.db"
    assert config.worktree_dir == state_dir / "worktrees"
    assert config.database_path.parent.is_dir()
    assert config.worktree_dir.is_dir()


def test_workspace_config_is_overridden_by_environment(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (tmp_path / ".agentbraid.toml").write_text(
        """
[agentbraid]
codex_model = "gpt-config"
max_parallel_codex = 2
max_task_attempts = 3
""".strip(),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENTBRAID_CODEX_MODEL", "gpt-environment")
    monkeypatch.setenv("AGENTBRAID_MAX_PARALLEL_CODEX", "4")

    config = AgentBraidConfig.load(tmp_path)

    assert config.codex_model == "gpt-environment"
    assert config.max_parallel_codex == 4
    assert config.max_task_attempts == 3


def test_config_rejects_unknown_keys(tmp_path: Path) -> None:
    (tmp_path / ".agentbraid.toml").write_text(
        "[agentbraid]\nlaunch_antigravity = true\n",
        encoding="utf-8",
    )

    with pytest.raises(ConfigurationError, match="unknown configuration keys"):
        AgentBraidConfig.load(tmp_path)


def test_config_rejects_non_positive_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENTBRAID_MAX_TASK_ATTEMPTS", "0")

    with pytest.raises(ConfigurationError, match="must be positive"):
        AgentBraidConfig.load()


def test_config_rejects_host_cli_as_codex_binary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENTBRAID_CODEX_BINARY", "agy")

    with pytest.raises(ConfigurationError, match="official codex executable"):
        AgentBraidConfig.load()
