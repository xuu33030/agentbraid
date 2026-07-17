from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from agentbraid.errors import WorktreeConflictError, WorktreeError
from agentbraid.worktrees import WorktreeManager


def git(repository: Path, *arguments: str, check: bool = True) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        capture_output=True,
        check=False,
        text=True,
    )
    if check and completed.returncode != 0:
        raise AssertionError(completed.stderr)
    return completed.stdout.strip()


def repository(tmp_path: Path) -> tuple[Path, WorktreeManager]:
    root = tmp_path / "repository"
    root.mkdir()
    git(root, "init", "-b", "main")
    git(root, "config", "user.name", "AgentBraid Test")
    git(root, "config", "user.email", "agentbraid@example.test")
    (root / "shared.txt").write_text("base\n", encoding="utf-8")
    git(root, "add", "shared.txt")
    git(root, "commit", "--signoff", "-m", "chore: initial state")
    return root, WorktreeManager(root, tmp_path / "worktrees")


def test_task_commit_integrates_without_touching_primary_workspace(tmp_path: Path) -> None:
    root, manager = repository(tmp_path)
    run_id = "run-one"
    integration_branch = "agentbraid/run-one"
    integration = manager.prepare_run(run_id, integration_branch)
    task = manager.prepare_task(run_id, "implement", integration_branch)
    (task.path / "feature.txt").write_text("implemented\n", encoding="utf-8")

    task_commit = manager.commit_task(task.path, "implement", "Implement feature")
    integrated_commit = manager.integrate_task(
        run_id,
        "implement",
        integration_branch,
        task_commit,
    )

    assert (integration.path / "feature.txt").read_text(encoding="utf-8") == "implemented\n"
    assert not (root / "feature.txt").exists()
    assert git(task.path, "show", "-s", "--format=%B", task_commit).count("Signed-off-by:") == 1
    assert integrated_commit == git(integration.path, "rev-parse", "HEAD")


def test_integrating_same_task_twice_is_idempotent(tmp_path: Path) -> None:
    _, manager = repository(tmp_path)
    run_id = "run-repeat"
    branch = "agentbraid/run-repeat"
    task = manager.prepare_task(run_id, "document", branch)
    (task.path / "notes.md").write_text("notes\n", encoding="utf-8")
    task_commit = manager.commit_task(task.path, "document", "Document result")

    first = manager.integrate_task(run_id, "document", branch, task_commit)
    second = manager.integrate_task(run_id, "document", branch, task_commit)

    assert second == first


def test_conflict_restores_integration_head_and_preserves_task_worktree(
    tmp_path: Path,
) -> None:
    _, manager = repository(tmp_path)
    run_id = "run-conflict"
    branch = "agentbraid/run-conflict"
    first = manager.prepare_task(run_id, "first", branch)
    second = manager.prepare_task(run_id, "second", branch)
    (first.path / "shared.txt").write_text("first\n", encoding="utf-8")
    (second.path / "shared.txt").write_text("second\n", encoding="utf-8")
    first_commit = manager.commit_task(first.path, "first", "First edit")
    second_commit = manager.commit_task(second.path, "second", "Second edit")
    manager.integrate_task(run_id, "first", branch, first_commit)
    integration_path = manager.integration_path(run_id)
    stable_head = git(integration_path, "rev-parse", "HEAD")

    with pytest.raises(WorktreeConflictError, match="integration conflicted"):
        manager.integrate_task(run_id, "second", branch, second_commit)

    assert git(integration_path, "rev-parse", "HEAD") == stable_head
    assert (integration_path / "shared.txt").read_text(encoding="utf-8") == "first\n"
    assert second.path.is_dir()
    assert (second.path / "shared.txt").read_text(encoding="utf-8") == "second\n"


def test_unsigned_host_commit_is_rejected(tmp_path: Path) -> None:
    _, manager = repository(tmp_path)
    run_id = "run-unsigned"
    branch = "agentbraid/run-unsigned"
    task = manager.prepare_task(run_id, "host-edit", branch)
    (task.path / "host.txt").write_text("host change\n", encoding="utf-8")
    git(task.path, "add", "host.txt")
    git(task.path, "commit", "-m", "feat: unsigned host change")
    commit_sha = git(task.path, "rev-parse", "HEAD")

    with pytest.raises(WorktreeError, match="DCO signoff"):
        manager.integrate_task(run_id, "host-edit", branch, commit_sha)

    assert task.path.is_dir()


def test_apply_integration_is_explicit_fast_forward(tmp_path: Path) -> None:
    root, manager = repository(tmp_path)
    run_id = "run-apply"
    branch = "agentbraid/run-apply"
    task = manager.prepare_task(run_id, "apply-task", branch)
    (task.path / "applied.txt").write_text("ready\n", encoding="utf-8")
    task_commit = manager.commit_task(task.path, "apply-task", "Prepare apply")
    integrated = manager.integrate_task(run_id, "apply-task", branch, task_commit)

    applied = manager.apply_integration(branch)

    assert applied == integrated
    assert (root / "applied.txt").read_text(encoding="utf-8") == "ready\n"
    assert git(root, "branch", "--show-current") == "main"


def test_dirty_primary_workspace_is_rejected(tmp_path: Path) -> None:
    root, manager = repository(tmp_path)
    (root / "dirty.txt").write_text("uncommitted\n", encoding="utf-8")

    with pytest.raises(WorktreeError, match="workspace must be clean"):
        manager.prepare_run("run-dirty", "agentbraid/run-dirty")


def test_dirty_existing_integration_worktree_is_rejected(tmp_path: Path) -> None:
    _, manager = repository(tmp_path)
    integration = manager.prepare_run("run-read-only", "agentbraid/run-read-only")
    (integration.path / "shared.txt").write_text("unexpected\n", encoding="utf-8")

    with pytest.raises(WorktreeError, match="managed worktree must remain clean"):
        manager.prepare_run("run-read-only", "agentbraid/run-read-only")


def test_apply_rejects_a_different_primary_branch(tmp_path: Path) -> None:
    root, manager = repository(tmp_path)
    target = manager.primary_target()
    task = manager.prepare_task("run-target", "change", "agentbraid/run-target")
    (task.path / "feature.txt").write_text("ready\n", encoding="utf-8")
    commit = manager.commit_task(task.path, "change", "Prepare feature")
    manager.integrate_task("run-target", "change", "agentbraid/run-target", commit)
    git(root, "switch", "-c", "other-branch")

    with pytest.raises(WorktreeError, match="branch changed"):
        manager.apply_integration_to_target(
            "agentbraid/run-target",
            expected_branch=target.branch,
            expected_commit=target.commit,
        )
