"""Run a real Codex-backed AgentBraid example; never applies the result."""

import argparse
import asyncio
import json
import subprocess
from pathlib import Path

from agentbraid.config import AgentBraidConfig
from agentbraid.models import RoutingMode, RunExecutionOverrides, StartRunRequest
from agentbraid.providers.codex import CodexAdapter
from agentbraid.service import AgentBraidService

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument(
    "directory", type=Path, help="Existing demo directory with a clean workspace Git repository"
)
parser.add_argument(
    "--ignore-user-config",
    action="store_true",
    help="Opt out of local Codex config for this invocation only",
)
parser.add_argument("--model", default=None, help="Explicit Codex model; otherwise CLI default")
args = parser.parse_args()
BASE = args.directory.resolve()
WORKSPACE = BASE / "workspace"
STATE = BASE / "state"


class LocalConfigIndependentCodex(CodexAdapter):
    """Real CLI adapter, with a documented per-invocation local config opt-out."""

    def _command(self, **kwargs):
        command = super()._command(**kwargs)
        if args.ignore_user_config:
            command.insert(2 if command[2] != "resume" else 3, "--ignore-user-config")
        return command


async def main():
    config = AgentBraidConfig(
        state_dir=STATE,
        database_path=STATE / "agentbraid.db",
        worktree_dir=STATE / "worktrees",
        codex_timeout_seconds=300,
        codex_model=args.model,
    )
    config.ensure_directories()
    service = AgentBraidService(config, WORKSPACE, codex=LocalConfigIndependentCodex(config))
    request = StartRunRequest(
        goal=(
            "Fix slugify so leading and trailing whitespace is ignored before replacing "
            "internal spaces with hyphens. Preserve lowercasing. Only change slug.py and "
            "test_slug.py. First add regression tests for leading/trailing whitespace, "
            "whitespace-only and empty inputs and run python3 -m unittest -v to demonstrate "
            "failures before fixing. Then implement minimal fix and rerun all tests. Record the "
            "exact expected failing red output in result.notes, NOT result.validations: "
            "AgentBraid requires every validation record on a successful task to pass. Put only "
            "final passing green commands in result.validations. The plan must contain exactly "
            "one implementation task and no separate review task. AgentBraid automatically "
            "performs final lead review with the full run snapshot including notes. Do not "
            "require a separate worker to read other task result metadata, because worker "
            "prompts do not include dependency results. The automatic final lead review must "
            "inspect the diff and independently run python3 -m unittest -v. Do not apply, push, "
            "publish or modify the primary workspace."
        ),
        constraints=[
            "No dependencies. No network or credential access by worker tools.",
            "Use only the assigned worktree. Do not commit; AgentBraid handles signed-off commits.",
        ],
        execution=RunExecutionOverrides(
            routing_mode=RoutingMode.CODEX_ONLY, max_task_attempts=1, codex_timeout_seconds=300
        ),
    )
    before = subprocess.check_output(
        ["git", "-C", str(WORKSPACE), "rev-parse", "HEAD"], text=True
    ).strip()
    run = service.create_run(request)
    (BASE / "run-id.txt").write_text(run.run_id + "\n")
    (BASE / "request.json").write_text(request.model_dump_json(indent=2) + "\n")
    print("RUN", run.run_id, flush=True)
    try:
        run = await service.execute_run(run.run_id)
    finally:
        run = service.store.get_run(run.run_id)
        (BASE / "snapshot.json").write_text(run.model_dump_json(indent=2) + "\n")
        after = subprocess.check_output(
            ["git", "-C", str(WORKSPACE), "rev-parse", "HEAD"], text=True
        ).strip()
        (BASE / "delivery.json").write_text(
            json.dumps(
                {
                    "before": before,
                    "after": after,
                    "primary_unchanged": before == after,
                    "human_confirmation": "pending",
                    "applied": False,
                },
                indent=2,
            )
            + "\n"
        )
        print("STATUS", run.status, "PRIMARY_UNCHANGED", before == after, flush=True)
    if run.status.value != "completed":
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
