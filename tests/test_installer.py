from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from agentbraid.cli import main
from agentbraid.errors import ConfigurationError
from agentbraid.installer import install_workspace_integration


def test_installer_creates_workspace_mcp_config_and_skill(tmp_path: Path) -> None:
    installed = install_workspace_integration(tmp_path)

    config_path = tmp_path / ".agents" / "mcp_config.json"
    skill_path = tmp_path / ".agents" / "skills" / "agentbraid" / "SKILL.md"
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    server = payload["mcpServers"]["agentbraid"]

    assert installed == [config_path, skill_path]
    assert server == {
        "args": ["-m", "agentbraid", "serve"],
        "command": sys.executable,
        "cwd": str(tmp_path),
        "env": {"AGENTBRAID_WORKSPACE": str(tmp_path)},
    }
    assert "agy" not in server["command"]
    assert "Google credentials" in skill_path.read_text(encoding="utf-8")
    assert "claim_host_task" in skill_path.read_text(encoding="utf-8")


def test_installer_preserves_other_servers_and_is_idempotent(tmp_path: Path) -> None:
    config_path = tmp_path / ".agents" / "mcp_config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "existing": {"command": "existing-mcp", "args": []},
                },
                "metadata": {"owner": "test"},
            }
        ),
        encoding="utf-8",
    )

    install_workspace_integration(tmp_path)
    first = config_path.read_text(encoding="utf-8")
    install_workspace_integration(tmp_path)
    second = config_path.read_text(encoding="utf-8")
    payload = json.loads(second)

    assert first == second
    assert payload["mcpServers"]["existing"]["command"] == "existing-mcp"
    assert payload["metadata"] == {"owner": "test"}


def test_installer_preflights_conflicting_skill_before_config_write(tmp_path: Path) -> None:
    config_path = tmp_path / ".agents" / "mcp_config.json"
    skill_path = tmp_path / ".agents" / "skills" / "agentbraid" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    skill_path.write_text("custom instructions\n", encoding="utf-8")

    with pytest.raises(ConfigurationError, match="skill already exists"):
        install_workspace_integration(tmp_path)

    assert not config_path.exists()
    assert skill_path.read_text(encoding="utf-8") == "custom instructions\n"


def test_force_replaces_only_agentbraid_entries(tmp_path: Path) -> None:
    config_path = tmp_path / ".agents" / "mcp_config.json"
    skill_path = tmp_path / ".agents" / "skills" / "agentbraid" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    config_path.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "agentbraid": {"command": "wrong"},
                    "existing": {"command": "keep"},
                }
            }
        ),
        encoding="utf-8",
    )
    skill_path.write_text("wrong\n", encoding="utf-8")

    install_workspace_integration(tmp_path, force=True)
    payload = json.loads(config_path.read_text(encoding="utf-8"))

    assert payload["mcpServers"]["agentbraid"]["command"] == sys.executable
    assert payload["mcpServers"]["existing"] == {"command": "keep"}
    assert skill_path.read_text(encoding="utf-8").startswith("---\nname: agentbraid")


def test_cli_init_prints_installed_paths(
    capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    exit_code = main(["init", str(tmp_path)])

    assert exit_code == 0
    assert capsys.readouterr().out.splitlines() == [
        str(tmp_path / ".agents" / "mcp_config.json"),
        str(tmp_path / ".agents" / "skills" / "agentbraid" / "SKILL.md"),
    ]
