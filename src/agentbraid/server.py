from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from agentbraid.models import (
    ApplyRunResult,
    HostTaskResult,
    RunSnapshot,
    StartRunRequest,
    TaskState,
)
from agentbraid.service import AgentBraidService, _assert_not_child


def create_server(service: AgentBraidService) -> FastMCP[None]:
    server: FastMCP[None] = FastMCP(
        "AgentBraid",
        instructions=(
            "Codex is the accountable lead. The active host claims only assigned host tasks "
            "and submits typed evidence; this server never launches the host CLI."
        ),
    )

    @server.tool(
        structured_output=True,
        annotations=ToolAnnotations(
            title="Start AgentBraid run",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=True,
        ),
    )
    async def start_run(request: StartRunRequest) -> RunSnapshot:
        """Plan and start a durable multi-agent run for the configured workspace."""
        return await service.start_run(request)

    @server.tool(
        structured_output=True,
        annotations=ToolAnnotations(
            title="Claim AgentBraid host task",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    def claim_host_task(
        run_id: str,
        host_id: str = "antigravity-host",
        task_id: str | None = None,
    ) -> TaskState | None:
        """Atomically claim the next ready task assigned to the active MCP host."""
        return service.claim_host_task(run_id, host_id, task_id=task_id)

    @server.tool(
        structured_output=True,
        annotations=ToolAnnotations(
            title="Submit AgentBraid host result",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=False,
        ),
    )
    async def submit_host_result(
        run_id: str,
        task_id: str,
        result: HostTaskResult,
        host_id: str = "antigravity-host",
    ) -> TaskState:
        """Submit the typed result and evidence for a claimed host task."""
        return await service.submit_host_result(run_id, task_id, host_id, result)

    @server.tool(
        structured_output=True,
        annotations=ToolAnnotations(
            title="Inspect AgentBraid run",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    def get_run(run_id: str) -> RunSnapshot:
        """Return the durable run plan, assignments, and latest task states."""
        return service.get_run(run_id)

    @server.tool(
        structured_output=True,
        annotations=ToolAnnotations(
            title="Cancel AgentBraid run",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    def cancel_run(run_id: str) -> RunSnapshot:
        """Cancel a run and all active tasks without deleting its history."""
        return service.cancel_run(run_id)

    @server.tool(
        structured_output=True,
        annotations=ToolAnnotations(
            title="List AgentBraid capabilities",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    def list_capabilities() -> list[dict[str, object]]:
        """List observed Codex and host capability health without credentials."""
        return [capability.model_dump(mode="json") for capability in service.list_capabilities()]

    @server.tool(
        structured_output=True,
        annotations=ToolAnnotations(
            title="Apply reviewed AgentBraid run",
            destructiveHint=True,
            idempotentHint=True,
            openWorldHint=False,
        ),
    )
    def apply_run(
        run_id: str,
        confirmation: Literal["apply-reviewed-run"],
    ) -> ApplyRunResult:
        """Explicitly fast-forward the primary workspace to a completed integration branch."""
        return service.apply_run(run_id, confirmation)

    return server


def run_server() -> None:
    _assert_not_child()
    workspace = Path(os.environ.get("AGENTBRAID_WORKSPACE", Path.cwd())).resolve()
    create_server(AgentBraidService.from_workspace(workspace)).run(transport="stdio")
