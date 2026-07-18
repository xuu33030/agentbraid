from __future__ import annotations

import re
import subprocess
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path

from agentbraid.errors import WorktreeConflictError, WorktreeError
from agentbraid.models import RunCleanupArtifact, RunCleanupPreview, RunSnapshot, RunStatus
from agentbraid.redaction import redact_text

_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_MAX_GIT_DETAIL = 20_000


@dataclass(frozen=True, slots=True)
class WorktreeInfo:
    path: Path
    branch: str
    base_commit: str


@dataclass(frozen=True, slots=True)
class PrimaryTarget:
    branch: str
    commit: str


class WorktreeManager:
    """Create, validate, and integrate local Git worktrees without remote delivery."""

    def __init__(self, workspace: Path, worktree_root: Path) -> None:
        self.workspace = workspace.expanduser().resolve()
        self.worktree_root = worktree_root.expanduser().resolve()

    def assert_clean_workspace(self) -> None:
        repository = self.repository_root()
        status = self._git(["status", "--porcelain=v1", "--untracked-files=all"], cwd=repository)
        if status:
            raise WorktreeError(
                "workspace must be clean before starting an AgentBraid run",
                detail=status,
            )

    def repository_root(self) -> Path:
        root = Path(self._git(["rev-parse", "--show-toplevel"], cwd=self.workspace)).resolve()
        if self.worktree_root == root or self.worktree_root.is_relative_to(root):
            raise WorktreeError(
                "AgentBraid worktrees must live outside the repository",
                detail=f"repository={root}, worktrees={self.worktree_root}",
            )
        return root

    def primary_target(self) -> PrimaryTarget:
        repository = self.repository_root()
        branch = self._git(["branch", "--show-current"], cwd=repository)
        if not branch:
            raise WorktreeError("primary workspace must be on a named branch")
        return PrimaryTarget(
            branch=branch,
            commit=self._git(["rev-parse", "HEAD"], cwd=repository),
        )

    def integration_path(self, run_id: str) -> Path:
        return self._safe_path(_safe_segment(run_id, "run ID"), "integration")

    def task_path(self, run_id: str, task_id: str) -> Path:
        return self._safe_path(
            _safe_segment(run_id, "run ID"),
            "tasks",
            _safe_segment(task_id, "task ID"),
        )

    def task_branch(self, run_id: str, task_id: str) -> str:
        safe_run_id = _safe_segment(run_id, "run ID")[:12]
        safe_task_id = _safe_segment(task_id, "task ID")
        return f"agentbraid/tasks/{safe_run_id}/{safe_task_id}"

    def prepare_run(
        self,
        run_id: str,
        integration_branch: str,
        *,
        base_commit: str | None = None,
    ) -> WorktreeInfo:
        self.assert_clean_workspace()
        repository = self.repository_root()
        path = self.integration_path(run_id)
        self._validate_branch(integration_branch)
        if path.exists():
            self._assert_worktree_branch(path, integration_branch)
            self.assert_clean_worktree(path)
            return WorktreeInfo(
                path=path,
                branch=integration_branch,
                base_commit=self._git(["rev-parse", "HEAD"], cwd=path),
            )

        path.parent.mkdir(parents=True, exist_ok=True)
        self._git(["worktree", "prune"], cwd=repository)
        if self._branch_exists(integration_branch):
            self._git(["worktree", "add", str(path), integration_branch], cwd=repository)
        else:
            self._git(
                [
                    "worktree",
                    "add",
                    "-b",
                    integration_branch,
                    str(path),
                    base_commit or "HEAD",
                ],
                cwd=repository,
            )
        return WorktreeInfo(
            path=path,
            branch=integration_branch,
            base_commit=self._git(["rev-parse", "HEAD"], cwd=path),
        )

    def prepare_task(
        self,
        run_id: str,
        task_id: str,
        integration_branch: str,
    ) -> WorktreeInfo:
        self.prepare_run(run_id, integration_branch)
        repository = self.repository_root()
        path = self.task_path(run_id, task_id)
        branch = self.task_branch(run_id, task_id)
        if path.exists():
            self._assert_worktree_branch(path, branch)
            return WorktreeInfo(
                path=path,
                branch=branch,
                base_commit=self._git(["merge-base", branch, integration_branch], cwd=repository),
            )

        path.parent.mkdir(parents=True, exist_ok=True)
        if self._branch_exists(branch):
            self._git(["worktree", "add", str(path), branch], cwd=repository)
        else:
            self._git(
                ["worktree", "add", "-b", branch, str(path), integration_branch],
                cwd=repository,
            )
        return WorktreeInfo(
            path=path,
            branch=branch,
            base_commit=self._git(["rev-parse", "HEAD"], cwd=path),
        )

    def commit_task(self, path: Path, task_id: str, title: str) -> str:
        resolved = path.expanduser().resolve()
        self._assert_managed_task_path(resolved)
        status = self._git(
            ["status", "--porcelain=v1", "--untracked-files=all"],
            cwd=resolved,
        )
        if not status:
            raise WorktreeError(f"mutating task produced no changes: {task_id}")
        self._git(["add", "--all"], cwd=resolved)
        message_title = redact_text(" ".join(title.split()))[:72] or task_id
        self._git(
            [
                "-c",
                "commit.gpgsign=false",
                "commit",
                "--signoff",
                "-m",
                f"agentbraid({task_id}): {message_title}",
            ],
            cwd=resolved,
        )
        commit_sha = self._git(["rev-parse", "HEAD"], cwd=resolved)
        if self._git(["status", "--porcelain=v1"], cwd=resolved):
            raise WorktreeError(f"task worktree is still dirty after commit: {task_id}")
        return commit_sha

    def integrate_task(
        self,
        run_id: str,
        task_id: str,
        integration_branch: str,
        commit_sha: str,
    ) -> str:
        task_path = self.task_path(run_id, task_id)
        task_branch = self.task_branch(run_id, task_id)
        integration = self.prepare_run(run_id, integration_branch)
        commits = self._validate_task_commits(
            task_path,
            task_branch,
            integration_branch,
            commit_sha,
        )
        integrated_messages = self._git(
            ["log", "--format=%B", integration_branch], cwd=integration.path
        )
        pending = [
            commit
            for commit in commits
            if f"(cherry picked from commit {commit})" not in integrated_messages
        ]
        if not pending:
            return self._git(["rev-parse", "HEAD"], cwd=integration.path)

        original_head = self._git(["rev-parse", "HEAD"], cwd=integration.path)
        try:
            for commit in pending:
                self._git(["cherry-pick", "-x", commit], cwd=integration.path)
        except WorktreeError as exc:
            self._git(["cherry-pick", "--abort"], cwd=integration.path, check=False)
            self._git(["reset", "--hard", original_head], cwd=integration.path)
            raise WorktreeConflictError(
                f"task integration conflicted: {task_id}",
                detail=exc.detail,
            ) from exc
        return self._git(["rev-parse", "HEAD"], cwd=integration.path)

    def apply_integration(self, integration_branch: str) -> str:
        return self.apply_integration_to_target(integration_branch)

    def apply_integration_to_target(
        self,
        integration_branch: str,
        *,
        expected_branch: str | None = None,
        expected_commit: str | None = None,
    ) -> str:
        self.validate_apply_target(
            integration_branch,
            expected_branch=expected_branch,
            expected_commit=expected_commit,
        )
        repository = self.repository_root()
        self._git(["merge", "--ff-only", integration_branch], cwd=repository)
        return self._git(["rev-parse", "HEAD"], cwd=repository)

    def validate_apply_target(
        self,
        integration_branch: str,
        *,
        expected_branch: str | None = None,
        expected_commit: str | None = None,
    ) -> PrimaryTarget:
        self.assert_clean_workspace()
        self._validate_branch(integration_branch)
        target = self.primary_target()
        if expected_branch is not None and target.branch != expected_branch:
            raise WorktreeError(
                "primary workspace branch changed since the run started",
                detail=f"expected={expected_branch}, actual={target.branch}",
            )
        if expected_commit is not None and target.commit != expected_commit:
            raise WorktreeError(
                "primary workspace commit changed since the run started",
                detail=f"expected={expected_commit}, actual={target.commit}",
            )
        if not self._branch_exists(integration_branch):
            raise WorktreeError("integration branch does not exist", detail=integration_branch)
        return target

    def preview_run_cleanup(self, run: RunSnapshot) -> RunCleanupPreview:
        blockers: list[str] = []
        artifacts: list[RunCleanupArtifact] = []
        if run.status not in {RunStatus.COMPLETED, RunStatus.CANCELLED, RunStatus.FAILED}:
            blockers.append(f"run status is not terminal: {run.status.value}")

        repository = self.repository_root()
        integration_branch = run.integration_branch
        paths: list[tuple[Path, str]] = [
            (self.integration_path(run.run_id), integration_branch or "")
        ]
        for task in run.tasks:
            expected = self.task_path(run.run_id, task.spec.task_id)
            if task.worktree_path is not None and Path(task.worktree_path).resolve() != expected:
                blockers.append(f"task worktree path is not managed: {task.spec.task_id}")
            paths.append((expected, self.task_branch(run.run_id, task.spec.task_id)))

        integration_branch_exists = bool(
            integration_branch and self._branch_exists(integration_branch)
        )
        task_branch_exists = {
            task.spec.task_id: self._branch_exists(self.task_branch(run.run_id, task.spec.task_id))
            for task in run.tasks
        }
        has_managed_artifacts = (
            any(path.exists() for path, _ in paths)
            or integration_branch_exists
            or any(task_branch_exists.values())
        )
        if has_managed_artifacts:
            if run.base_commit is None:
                blockers.append(
                    "repository identity cannot be verified without the run base commit"
                )
            elif not self._commit_exists(run.base_commit):
                blockers.append("run base commit is missing from the current repository")

        for path, branch in paths:
            removable = True
            detail: str | None = None
            if path.exists():
                try:
                    if branch:
                        self._assert_worktree_branch(path, branch)
                    self.assert_clean_worktree(path)
                except WorktreeError as exc:
                    removable = False
                    detail = exc.message
                    blockers.append(f"worktree is not safely removable: {path}")
            artifacts.append(
                RunCleanupArtifact(
                    kind="worktree",
                    identifier=str(path),
                    removable=removable,
                    detail=detail,
                )
            )

        if integration_branch and integration_branch_exists:
            merged = (
                self._run_git(
                    ["merge-base", "--is-ancestor", integration_branch, "HEAD"],
                    cwd=repository,
                ).returncode
                == 0
            )
            if not merged:
                blockers.append(f"integration branch is not merged: {integration_branch}")
            artifacts.append(
                RunCleanupArtifact(
                    kind="branch",
                    identifier=integration_branch,
                    removable=merged,
                    detail=None if merged else "branch contains unapplied work",
                )
            )

        comparison = (
            integration_branch if integration_branch and integration_branch_exists else "HEAD"
        )
        for task in run.tasks:
            branch = self.task_branch(run.run_id, task.spec.task_id)
            if not task_branch_exists[task.spec.task_id]:
                continue
            cherry = self._git(["cherry", comparison, branch], cwd=repository)
            equivalent = not any(line.startswith("+") for line in cherry.splitlines())
            if not equivalent:
                blockers.append(f"task branch has unique work: {branch}")
            artifacts.append(
                RunCleanupArtifact(
                    kind="branch",
                    identifier=branch,
                    removable=equivalent,
                    detail=None if equivalent else "branch contains unique patches",
                )
            )

        deletable = not blockers
        artifacts.insert(
            0,
            RunCleanupArtifact(
                kind="database",
                identifier=run.run_id,
                removable=deletable,
                detail=None if deletable else "record preserved until local cleanup is safe",
            ),
        )
        return RunCleanupPreview(
            run_id=run.run_id,
            deletable=deletable,
            blockers=blockers,
            artifacts=artifacts,
        )

    def cleanup_run_artifacts(self, run: RunSnapshot) -> RunCleanupPreview:
        preview = self.preview_run_cleanup(run)
        if not preview.deletable:
            raise WorktreeError(
                "run artifacts are not safely removable",
                detail="; ".join(preview.blockers),
            )
        repository = self.repository_root()
        paths = [self.task_path(run.run_id, task.spec.task_id) for task in run.tasks]
        paths.append(self.integration_path(run.run_id))
        for path in paths:
            if path.exists():
                self._git(["worktree", "remove", str(path)], cwd=repository)
        for task in run.tasks:
            branch = self.task_branch(run.run_id, task.spec.task_id)
            if self._branch_exists(branch):
                self._git(["branch", "-D", branch], cwd=repository)
        if run.integration_branch and self._branch_exists(run.integration_branch):
            self._git(["branch", "-d", run.integration_branch], cwd=repository)
        self._git(["worktree", "prune"], cwd=repository)
        run_root = self.worktree_root / _safe_segment(run.run_id, "run ID")
        for candidate in (run_root / "tasks", run_root):
            with suppress(OSError):
                candidate.rmdir()
        return preview

    def assert_clean_worktree(self, path: Path) -> None:
        resolved = path.expanduser().resolve()
        status = self._git(
            ["status", "--porcelain=v1", "--untracked-files=all"],
            cwd=resolved,
        )
        if status:
            raise WorktreeError("managed worktree must remain clean", detail=status)

    def _validate_task_commits(
        self,
        task_path: Path,
        task_branch: str,
        integration_branch: str,
        commit_sha: str,
    ) -> list[str]:
        self._assert_worktree_branch(task_path, task_branch)
        head = self._git(["rev-parse", "HEAD"], cwd=task_path)
        if head != commit_sha:
            raise WorktreeError(
                "submitted task commit must be the task worktree HEAD",
                detail=f"expected={head}, received={commit_sha}",
            )
        base = self._git(["merge-base", task_branch, integration_branch], cwd=task_path)
        commits_output = self._git(
            ["rev-list", "--reverse", "--ancestry-path", f"{base}..{commit_sha}"],
            cwd=task_path,
        )
        commits = commits_output.splitlines()
        if not commits:
            raise WorktreeError("task branch contains no commits to integrate")
        merge_commits = self._git(
            ["rev-list", "--merges", f"{base}..{commit_sha}"],
            cwd=task_path,
        )
        if merge_commits:
            raise WorktreeError("task branches must not contain merge commits")
        for commit in commits:
            message = self._git(["show", "-s", "--format=%B", commit], cwd=task_path)
            if not any(line.startswith("Signed-off-by: ") for line in message.splitlines()):
                raise WorktreeError(
                    "task commits must include a DCO signoff",
                    detail=f"commit={commit}",
                )
        return commits

    def _assert_managed_task_path(self, path: Path) -> None:
        tasks_root = self.worktree_root
        if not path.is_relative_to(tasks_root):
            raise WorktreeError(
                "task worktree is outside the configured worktree root",
                detail=str(path),
            )
        self._git(["rev-parse", "--is-inside-work-tree"], cwd=path)

    def _assert_worktree_branch(self, path: Path, expected_branch: str) -> None:
        if not path.is_dir():
            raise WorktreeError(f"expected worktree is missing: {path}")
        actual = self._git(["branch", "--show-current"], cwd=path)
        if actual != expected_branch:
            raise WorktreeError(
                "worktree branch does not match AgentBraid state",
                detail=f"path={path}, expected={expected_branch}, actual={actual}",
            )

    def _branch_exists(self, branch: str) -> bool:
        completed = self._run_git(
            ["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"],
            cwd=self.repository_root(),
        )
        return completed.returncode == 0

    def _commit_exists(self, commit: str) -> bool:
        completed = self._run_git(
            ["cat-file", "-e", f"{commit}^{{commit}}"],
            cwd=self.repository_root(),
        )
        return completed.returncode == 0

    def _validate_branch(self, branch: str) -> None:
        completed = self._run_git(["check-ref-format", "--branch", branch], cwd=self.workspace)
        if completed.returncode != 0:
            raise WorktreeError("invalid integration branch name", detail=branch)

    def _safe_path(self, *segments: str) -> Path:
        path = self.worktree_root.joinpath(*segments).resolve()
        if not path.is_relative_to(self.worktree_root):
            raise WorktreeError("worktree path escaped the configured root")
        return path

    def _git(
        self,
        arguments: list[str],
        *,
        cwd: Path,
        check: bool = True,
    ) -> str:
        completed = self._run_git(arguments, cwd=cwd)
        if check and completed.returncode != 0:
            detail = (completed.stderr or completed.stdout).strip()
            raise WorktreeError(
                f"git command failed: git {' '.join(arguments[:3])}",
                detail=_truncate(detail),
            )
        return completed.stdout.strip()

    @staticmethod
    def _run_git(arguments: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                ["git", "-C", str(cwd), *arguments],
                capture_output=True,
                check=False,
                text=True,
                timeout=120,
            )
        except (FileNotFoundError, OSError, subprocess.TimeoutExpired) as exc:
            raise WorktreeError("could not execute Git", detail=str(exc)) from exc


def _safe_segment(value: str, label: str) -> str:
    if not _SAFE_SEGMENT.fullmatch(value):
        raise WorktreeError(f"invalid {label}", detail=value)
    return value


def _truncate(value: str) -> str:
    if len(value) <= _MAX_GIT_DETAIL:
        return value
    return value[:_MAX_GIT_DETAIL] + "…"
