from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from agentbraid import __version__


@dataclass(frozen=True, slots=True)
class CheckResult:
    name: str
    ok: bool
    detail: str


def _command_version(command: str, *args: str) -> CheckResult:
    executable = shutil.which(command)
    if executable is None:
        return CheckResult(command, False, "not found on PATH")
    completed = subprocess.run(
        [executable, *args],
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )
    output = (completed.stdout or completed.stderr).strip().splitlines()
    detail = output[0] if output else f"exit code {completed.returncode}"
    return CheckResult(command, completed.returncode == 0, detail)


def _workspace_check(path: Path) -> CheckResult:
    completed = subprocess.run(
        ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
        capture_output=True,
        check=False,
        text=True,
        timeout=10,
    )
    if completed.returncode != 0:
        return CheckResult("workspace", False, "not inside a Git repository")
    return CheckResult("workspace", True, completed.stdout.strip())


def doctor(path: Path) -> list[CheckResult]:
    return [
        CheckResult("python", sys.version_info >= (3, 11), sys.version.split()[0]),
        _command_version("git", "--version"),
        _command_version("codex", "--version"),
        _workspace_check(path),
    ]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agentbraid",
        description="Braid multiple agents into one accountable workflow.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor_parser = subparsers.add_parser("doctor", help="Check local runtime prerequisites.")
    doctor_parser.add_argument("path", nargs="?", type=Path, default=Path.cwd())
    doctor_parser.add_argument("--json", action="store_true", dest="as_json")

    subparsers.add_parser("serve", help="Start the AgentBraid MCP server over stdio.")
    init_parser = subparsers.add_parser("init", help="Install host integration in a workspace.")
    init_parser.add_argument("path", nargs="?", type=Path, default=Path.cwd())
    init_parser.add_argument("--force", action="store_true")
    return parser


def _run_doctor(path: Path, as_json: bool) -> int:
    results = doctor(path.resolve())
    if as_json:
        print(json.dumps([asdict(result) for result in results], indent=2))
    else:
        for result in results:
            marker = "ok" if result.ok else "error"
            print(f"[{marker}] {result.name}: {result.detail}")
    return 0 if all(result.ok for result in results) else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "doctor":
        return _run_doctor(args.path, args.as_json)
    if args.command == "serve":
        from agentbraid.server import run_server

        run_server()
        return 0
    if args.command == "init":
        from agentbraid.installer import install_workspace_integration

        installed = install_workspace_integration(args.path.resolve(), force=args.force)
        for path in installed:
            print(path)
        return 0
    parser.error(f"unknown command: {args.command}")
    return 2
