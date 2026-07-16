from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from agentbraid.config import AgentBraidConfig
from agentbraid.errors import ProviderOutputError, ProviderUnavailableError
from agentbraid.models import RunPlan, StartRunRequest, TaskKind, TaskSpec, WorkerResult
from agentbraid.providers.codex import CodexAdapter, _classify_failure, _parse_jsonl


class FakeStdin:
    def __init__(self) -> None:
        self.payload = b""
        self.closed = False

    def write(self, payload: bytes) -> None:
        self.payload += payload

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True

    async def wait_closed(self) -> None:
        return None


class FakeProcess:
    def __init__(self, stdout: bytes, stderr: bytes = b"", return_code: int = 0) -> None:
        self.stdin = FakeStdin()
        self.stdout = asyncio.StreamReader()
        self.stdout.feed_data(stdout)
        self.stdout.feed_eof()
        self.stderr = asyncio.StreamReader()
        self.stderr.feed_data(stderr)
        self.stderr.feed_eof()
        self.returncode: int | None = None
        self._return_code = return_code
        self.terminated = False
        self.killed = False

    async def wait(self) -> int:
        self.returncode = self._return_code
        return self._return_code

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


def config(tmp_path: Path, *, max_output_bytes: int = 100_000) -> AgentBraidConfig:
    return AgentBraidConfig(
        state_dir=tmp_path / "state",
        database_path=tmp_path / "state" / "agentbraid.db",
        worktree_dir=tmp_path / "state" / "worktrees",
        codex_binary="codex",
        codex_model="gpt-test",
        codex_timeout_seconds=5,
        max_output_bytes=max_output_bytes,
    )


def run_plan_payload() -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "summary": "Inspect the repository.",
        "tasks": [
            {
                "task_id": "inspect",
                "title": "Inspect repository",
                "instructions": "Inspect the repository structure.",
                "kind": "exploration",
                "preferred_executor": "codex",
                "mutates_workspace": False,
                "dependencies": [],
                "acceptance_criteria": ["Repository structure is summarized."],
                "required_capabilities": [],
                "risk": "low",
                "complexity": 1,
                "max_attempts": 2,
            }
        ],
        "final_acceptance_criteria": ["The repository is understood."],
    }


def jsonl(*events: dict[str, object]) -> bytes:
    return b"\n".join(json.dumps(event).encode() for event in events) + b"\n"


@pytest.mark.asyncio
async def test_plan_invokes_structured_read_only_codex(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("GOOGLE_API_KEY", "google-secret-value")
    monkeypatch.setenv("GITHUB_TOKEN", "github-secret-value")
    monkeypatch.setenv("OPENAI_API_KEY", "openai-secret-value")
    monkeypatch.setenv("AGENTBRAID_TEST_CONTEXT", "safe-value")
    process = FakeProcess(
        jsonl(
            {"type": "thread.started", "thread_id": "thread-123"},
            {"type": "turn.started"},
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 100,
                    "cached_input_tokens": 40,
                    "output_tokens": 20,
                    "reasoning_output_tokens": 5,
                },
            },
        )
    )
    invocation: dict[str, Any] = {}

    async def create_process(*args: str, **kwargs: Any) -> FakeProcess:
        invocation["args"] = args
        invocation["kwargs"] = kwargs
        output_index = args.index("--output-last-message") + 1
        Path(args[output_index]).write_text(json.dumps(run_plan_payload()), encoding="utf-8")
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    adapter = CodexAdapter(config(tmp_path))

    result = await adapter.plan(StartRunRequest(goal="Inspect this repository."), tmp_path)

    assert isinstance(result.output, RunPlan)
    assert result.thread_id == "thread-123"
    assert result.usage.input_tokens == 100
    assert result.usage.cached_input_tokens == 40
    assert invocation["args"][:3] == ("codex", "exec", "--json")
    assert "read-only" in invocation["args"]
    assert invocation["kwargs"]["cwd"] == tmp_path
    assert invocation["kwargs"]["env"]["AGENTBRAID_CHILD"] == "1"
    assert "GOOGLE_API_KEY" not in invocation["kwargs"]["env"]
    assert "GITHUB_TOKEN" not in invocation["kwargs"]["env"]
    assert "OPENAI_API_KEY" not in invocation["kwargs"]["env"]
    assert invocation["kwargs"]["env"]["AGENTBRAID_TEST_CONTEXT"] == "safe-value"
    assert b"Inspect this repository." in process.stdin.payload
    assert process.stdin.closed is True


@pytest.mark.asyncio
async def test_resume_uses_existing_thread_when_stream_omits_thread_event(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    process = FakeProcess(jsonl({"type": "turn.started"}, {"type": "turn.completed"}))
    invocation: dict[str, Any] = {}

    async def create_process(*args: str, **kwargs: Any) -> FakeProcess:
        invocation["args"] = args
        output_index = args.index("--output-last-message") + 1
        Path(args[output_index]).write_text(json.dumps(run_plan_payload()), encoding="utf-8")
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)

    result = await CodexAdapter(config(tmp_path)).resume_lead(
        "thread-existing",
        "Revise the plan.",
        tmp_path,
        RunPlan,
    )

    assert result.thread_id == "thread-existing"
    assert invocation["args"][:4] == ("codex", "exec", "resume", "--json")
    assert invocation["args"][-2:] == ("thread-existing", "-")


@pytest.mark.asyncio
async def test_output_limit_terminates_codex(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    process = FakeProcess(b"x" * 21)

    async def create_process(*args: str, **kwargs: Any) -> FakeProcess:
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)

    with pytest.raises(ProviderOutputError, match="stdout exceeded"):
        await CodexAdapter(config(tmp_path, max_output_bytes=20)).plan(
            StartRunRequest(goal="Inspect."),
            tmp_path,
        )


@pytest.mark.asyncio
async def test_empty_stream_auth_failure_is_classified(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    process = FakeProcess(b"", stderr=b"Not logged in; run codex login", return_code=1)

    async def create_process(*args: str, **kwargs: Any) -> FakeProcess:
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)

    with pytest.raises(ProviderUnavailableError, match="authentication") as raised:
        await CodexAdapter(config(tmp_path)).plan(
            StartRunRequest(goal="Inspect."),
            tmp_path,
        )

    assert raised.value.retryable is False


@pytest.mark.asyncio
async def test_mutating_worker_uses_workspace_write_without_committing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    process = FakeProcess(
        jsonl(
            {"type": "thread.started", "thread_id": "worker-thread"},
            {"type": "turn.completed"},
        )
    )
    invocation: dict[str, Any] = {}

    async def create_process(*args: str, **kwargs: Any) -> FakeProcess:
        invocation["args"] = args
        output_index = args.index("--output-last-message") + 1
        Path(args[output_index]).write_text(
            json.dumps(
                {
                    "outcome": "succeeded",
                    "summary": "Worker completed the task.",
                    "changed_files": ["feature.py"],
                    "validations": [{"command": "pytest -q", "passed": True, "output": "passed"}],
                    "notes": [],
                    "confidence": 0.9,
                    "error": None,
                }
            ),
            encoding="utf-8",
        )
        return process

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create_process)
    task = TaskSpec(
        task_id="worker-task",
        title="Implement feature",
        instructions="Implement and validate the feature.",
        kind=TaskKind.IMPLEMENTATION,
        mutates_workspace=True,
        acceptance_criteria=["Tests pass."],
    )

    result = await CodexAdapter(config(tmp_path)).execute_task(
        task,
        tmp_path,
        run_id="run-test",
    )

    assert isinstance(result.output, WorkerResult)
    assert "workspace-write" in invocation["args"]
    assert b"do not create a Git commit" in process.stdin.payload


def test_invalid_jsonl_is_rejected() -> None:
    with pytest.raises(ProviderOutputError, match="invalid JSONL"):
        _parse_jsonl(b"not-json\n")


def test_quota_failure_is_retryable() -> None:
    error = _classify_failure(1, b"Usage limit reached", [])

    assert isinstance(error, ProviderUnavailableError)
    assert error.retryable is True
    assert error.quota_limited is True
