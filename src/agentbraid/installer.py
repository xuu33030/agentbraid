from __future__ import annotations

from pathlib import Path


def install_workspace_integration(workspace: Path, *, force: bool = False) -> list[Path]:
    del workspace, force
    raise SystemExit("AgentBraid host integration is not implemented in this bootstrap commit.")
