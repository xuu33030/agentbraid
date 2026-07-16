from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from agentbraid.config import AgentBraidConfig
from agentbraid.server import create_server
from agentbraid.service import AgentBraidService


@pytest.mark.asyncio
async def test_server_exposes_versioned_host_run_lifecycle(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    config = AgentBraidConfig(
        state_dir=state_dir,
        database_path=state_dir / "agentbraid.db",
        worktree_dir=state_dir / "worktrees",
    )
    server = create_server(AgentBraidService(config, tmp_path))

    tools = await server.list_tools()
    names = {tool.name for tool in tools}

    assert names == {
        "start_run",
        "claim_host_task",
        "submit_host_result",
        "get_run",
        "cancel_run",
        "list_capabilities",
    }
    start_tool = next(tool for tool in tools if tool.name == "start_run")
    assert "request" in start_tool.inputSchema["properties"]
    submit_tool = next(tool for tool in tools if tool.name == "submit_host_result")
    assert "result" in submit_tool.inputSchema["properties"]


@pytest.mark.asyncio
async def test_stdio_server_completes_mcp_handshake(tmp_path: Path) -> None:
    environment = {
        name: value
        for name, value in os.environ.items()
        if not name.startswith(("COV_CORE_", "COVERAGE_"))
    }
    environment.pop("AGENTBRAID_CHILD", None)
    environment["AGENTBRAID_WORKSPACE"] = str(tmp_path)
    environment["AGENTBRAID_STATE_DIR"] = str(tmp_path / "state")
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "agentbraid", "serve"],
        cwd=tmp_path,
        env=environment,
    )

    with Path(os.devnull).open("w", encoding="utf-8") as error_log:
        async with stdio_client(parameters, errlog=error_log) as streams:
            async with ClientSession(*streams) as session:
                initialized = await session.initialize()
                tools = await session.list_tools()

    assert initialized.serverInfo.name == "AgentBraid"
    assert "claim_host_task" in {tool.name for tool in tools.tools}
