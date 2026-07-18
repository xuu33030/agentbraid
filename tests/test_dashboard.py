from __future__ import annotations

import subprocess
from pathlib import Path
from threading import Event

import pytest
from starlette.testclient import TestClient

from agentbraid.config import AgentBraidConfig
from agentbraid.dashboard import (
    DashboardController,
    DashboardSecurity,
    create_dashboard_app,
    summarize_usage,
)
from agentbraid.errors import RunNotFoundError
from agentbraid.models import (
    Executor,
    LocalizedRunNames,
    ProviderInvocationOutcome,
    ProviderUsageRecord,
    RoutingDecision,
    RunPlan,
    RunStatus,
    StartRunRequest,
    TaskKind,
    TaskSpec,
    WorkspaceSettings,
)
from agentbraid.store import StateStore
from agentbraid.worktrees import WorktreeManager


def git(repository: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", "-C", str(repository), *arguments],
        capture_output=True,
        check=False,
        text=True,
    )
    if completed.returncode != 0:
        raise AssertionError(completed.stderr)
    return completed.stdout.strip()


def repository(tmp_path: Path) -> Path:
    workspace = tmp_path / "repository"
    workspace.mkdir()
    git(workspace, "init", "-b", "main")
    git(workspace, "config", "user.name", "AgentBraid Test")
    git(workspace, "config", "user.email", "agentbraid@example.test")
    (workspace / "README.md").write_text("# Dashboard test\n", encoding="utf-8")
    git(workspace, "add", "README.md")
    git(workspace, "commit", "--signoff", "-m", "chore: initialize dashboard test")
    return workspace


def dashboard_config(tmp_path: Path) -> AgentBraidConfig:
    state_dir = tmp_path / "state"
    return AgentBraidConfig(
        state_dir=state_dir,
        database_path=state_dir / "agentbraid.db",
        worktree_dir=state_dir / "worktrees",
        codex_model="gpt-test",
    )


def dashboard_client(
    config: AgentBraidConfig,
    store: StateStore,
    workspace: Path | None,
) -> tuple[TestClient, DashboardSecurity]:
    controller = DashboardController(config, store, initial_workspace=workspace)
    security = DashboardSecurity.create(
        "http://testserver",
        frozenset({"testserver"}),
    )
    client = TestClient(
        create_dashboard_app(controller, security),
        base_url="http://testserver",
        follow_redirects=False,
    )
    return client, security


def authenticate(client: TestClient, security: DashboardSecurity) -> None:
    response = client.get(f"/bootstrap?token={security.bootstrap_token}")
    assert response.status_code == 303


def single_task_plan() -> tuple[RunPlan, dict[str, RoutingDecision]]:
    task = TaskSpec(
        task_id="dashboard-task",
        title="Prepare Dashboard delivery",
        instructions="Prepare the reviewed integration branch.",
        kind=TaskKind.IMPLEMENTATION,
        preferred_executor=Executor.CODEX,
        mutates_workspace=True,
        acceptance_criteria=["The reviewed file exists."],
    )
    plan = RunPlan(
        summary="Prepare one reviewed task.",
        tasks=[task],
        final_acceptance_criteria=["The integration branch is ready."],
    )
    return plan, {
        task.task_id: RoutingDecision(
            executor=Executor.CODEX,
            score=1.0,
            rationale="Codex prepares the reviewed branch.",
        )
    }


def test_usage_summary_avoids_double_counting_and_attributes_retries() -> None:
    records = [
        ProviderUsageRecord(
            phase="task",
            executor=Executor.CODEX,
            model="gpt-test",
            task_id="retry-task",
            attempt=1,
            outcome=ProviderInvocationOutcome.FAILED,
            input_tokens=100,
            cached_input_tokens=40,
            output_tokens=20,
            reasoning_output_tokens=5,
            duration_seconds=1.5,
        ),
        ProviderUsageRecord(
            phase="task",
            executor=Executor.CODEX,
            model="gpt-test",
            task_id="retry-task",
            attempt=2,
            outcome=ProviderInvocationOutcome.SUCCEEDED,
            input_tokens=60,
            cached_input_tokens=80,
            output_tokens=10,
            reasoning_output_tokens=20,
            duration_seconds=0.5,
        ),
    ]

    summary = summarize_usage(records)
    totals = summary["totals"]

    assert isinstance(totals, dict)
    assert totals["observed_total_tokens"] == 190
    assert totals["cached_input_tokens"] == 100
    assert totals["reasoning_output_tokens"] == 15
    assert totals["retry_tokens"] == 120
    assert totals["legacy_invocation_count"] == 0


def test_planning_and_review_usage_do_not_require_task_attempts() -> None:
    summary = summarize_usage(
        [
            ProviderUsageRecord(
                phase="planning",
                executor=Executor.CODEX,
                model="gpt-test",
                outcome=ProviderInvocationOutcome.SUCCEEDED,
                input_tokens=10,
                output_tokens=2,
            ),
            ProviderUsageRecord(
                phase="review",
                executor=Executor.CODEX,
                model="gpt-test",
                outcome=ProviderInvocationOutcome.APPROVED,
                input_tokens=8,
                output_tokens=1,
            ),
        ]
    )

    assert isinstance(summary["totals"], dict)
    assert summary["totals"]["legacy_invocation_count"] == 0


def test_dashboard_requires_bootstrap_session_and_security_headers(tmp_path: Path) -> None:
    config = dashboard_config(tmp_path)
    store = StateStore(config.database_path)
    client, security = dashboard_client(config, store, None)

    unauthorized = client.get("/api/v1/meta")
    assert unauthorized.status_code == 403
    assert client.get("/assets/locales.json").status_code == 403
    authenticated = client.get(f"/bootstrap?token={security.bootstrap_token}")
    assert authenticated.status_code == 303
    assert authenticated.headers["location"] == "/"
    assert "HttpOnly" in authenticated.headers["set-cookie"]
    assert "SameSite=strict" in authenticated.headers["set-cookie"]
    assert client.get(f"/bootstrap?token={security.bootstrap_token}").status_code == 403

    index = client.get("/")
    assert index.status_code == 200
    assert "AgentBraid Dashboard" in index.text
    assert "frame-ancestors 'none'" in index.headers["content-security-policy"]
    assert index.headers["cache-control"] == "no-store"
    assert client.get("/assets/app.css").status_code == 200
    script = client.get("/assets/app.js")
    assert script.status_code == 200
    assert "innerHTML" not in script.text
    assert "agentbraid_dashboard_locale" in script.text
    assert "localStorage" not in script.text
    locales = client.get("/assets/locales.json")
    assert locales.status_code == 200
    assert locales.headers["content-type"].startswith("application/json")
    assert client.get("/assets/missing.json").status_code == 404


def test_dashboard_locales_have_matching_complete_key_sets(tmp_path: Path) -> None:
    config = dashboard_config(tmp_path)
    store = StateStore(config.database_path)
    client, security = dashboard_client(config, store, None)
    authenticate(client, security)

    locales = client.get("/assets/locales.json").json()
    assert set(locales) == {"en", "zh-TW", "zh-CN"}
    key_sets = {locale: set(messages) for locale, messages in locales.items()}
    assert key_sets["en"] == key_sets["zh-TW"] == key_sets["zh-CN"]
    assert {
        "app.title",
        "control.languageAria",
        "sidebar.showRuns",
        "actions.applyBlocked",
        "status.completed",
        "phase.task",
        "event.run.status_changed",
        "deliveryMode.integration_branch",
        "action.deleteSelected",
        "form.goalPlaceholder",
        "cleanup.worktree",
        "routingMode.codex_only",
        "toast.languageChanged",
    } <= key_sets["en"]
    assert all(
        isinstance(message, str) and message.strip()
        for messages in locales.values()
        for message in messages.values()
    )


def test_dashboard_can_start_a_background_run(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = repository(tmp_path)
    config = dashboard_config(tmp_path)
    store = StateStore(config.database_path)
    controller = DashboardController(config, store, initial_workspace=workspace)
    executed = Event()

    class StubService:
        def create_run(self, request: StartRunRequest) -> object:
            return store.create_run(
                request,
                base_branch="main",
                base_commit=git(workspace, "rev-parse", "HEAD"),
            )

        async def execute_run(self, run_id: str) -> object:
            store.begin_planning(run_id)
            executed.set()
            return store.get_run(run_id)

    stub = StubService()
    monkeypatch.setattr(controller, "_service_for_workspace", lambda _: stub)
    security = DashboardSecurity.create("http://testserver", frozenset({"testserver"}))
    headers = {
        "Origin": security.origin,
        "X-AgentBraid-CSRF": security.csrf_token,
    }
    with TestClient(
        create_dashboard_app(controller, security),
        base_url="http://testserver",
        follow_redirects=False,
    ) as client:
        authenticate(client, security)
        response = client.post(
            "/api/v1/runs",
            headers=headers,
            json={
                "request": {
                    "goal": "Inspect README without editing files.",
                    "workspace": str(workspace),
                    "delivery_mode": "report_only",
                },
                "save_defaults": False,
            },
        )

        assert response.status_code == 202
        run_id = response.json()["run"]["run_id"]
        assert executed.wait(timeout=2)
        assert store.get_run(run_id).status == RunStatus.PLANNING


def test_dashboard_settings_and_model_options_are_workspace_scoped(tmp_path: Path) -> None:
    workspace = repository(tmp_path)
    config = dashboard_config(tmp_path)
    store = StateStore(config.database_path)
    run = store.create_run(
        StartRunRequest(goal="Observe a model.", workspace=str(workspace)),
        run_id="model-run",
    )
    store.record_provider_usage(
        run.run_id,
        ProviderUsageRecord(
            phase="planning",
            executor=Executor.CODEX,
            model="observed-codex",
        ),
    )
    client, security = dashboard_client(config, store, workspace)
    authenticate(client, security)
    headers = {
        "Origin": security.origin,
        "X-AgentBraid-CSRF": security.csrf_token,
    }

    options = client.get("/api/v1/model-options").json()
    settings_payload = client.get("/api/v1/settings", params={"workspace": str(workspace)}).json()
    settings_payload["settings"]["codex_model"] = "dashboard-codex"
    settings_payload["settings"]["host_model"] = "agy-routing-label"
    saved = client.put(
        "/api/v1/settings",
        headers=headers,
        json={"settings": settings_payload["settings"]},
    )

    assert options["codex"] == ["observed-codex"]
    assert options["host_controlled_by_dashboard"] is False
    assert settings_payload["database_path"] == str(config.database_path.resolve())
    assert saved.status_code == 200
    assert saved.json()["settings"]["codex_model"] == "dashboard-codex"
    assert saved.json()["settings"]["host_model"] == "agy-routing-label"
    assert saved.json()["requires_restart"] is False

    changed = saved.json()["settings"]
    changed["worktree_dir"] = str(tmp_path / "alternate-worktrees")
    restart = client.put(
        "/api/v1/settings",
        headers=headers,
        json={"settings": changed},
    )
    assert restart.status_code == 200
    assert restart.json()["requires_restart"] is True


def test_dashboard_environment_settings_remain_locked(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    workspace = repository(tmp_path)
    config = dashboard_config(tmp_path)
    monkeypatch.setenv("AGENTBRAID_CODEX_MODEL", "locked-by-environment")
    config = config.__class__(
        state_dir=config.state_dir,
        database_path=config.database_path,
        worktree_dir=config.worktree_dir,
        codex_model="locked-by-environment",
    )
    controller = DashboardController(
        config,
        StateStore(config.database_path),
        initial_workspace=workspace,
    )
    payload = controller.get_settings(str(workspace))
    attempted = WorkspaceSettings.model_validate(payload["settings"]).model_copy(
        update={"codex_model": "browser-override"}
    )

    saved = controller.save_settings(attempted)

    locked_fields = saved["locked_fields"]
    saved_settings = saved["settings"]
    assert isinstance(locked_fields, list)
    assert isinstance(saved_settings, dict)
    assert "codex_model" in locked_fields
    assert saved_settings["codex_model"] == "locked-by-environment"


def test_dashboard_rename_preview_and_delete_run_with_local_data(tmp_path: Path) -> None:
    workspace = repository(tmp_path)
    config = dashboard_config(tmp_path)
    store = StateStore(config.database_path)
    run = store.create_run(
        StartRunRequest(goal="Disposable run.", workspace=str(workspace)),
        run_id="disposable-run",
        base_branch="main",
        base_commit=git(workspace, "rev-parse", "HEAD"),
    )
    store.set_run_status(run.run_id, RunStatus.FAILED, error="Expected test failure")
    client, security = dashboard_client(config, store, workspace)
    authenticate(client, security)
    headers = {
        "Origin": security.origin,
        "X-AgentBraid-CSRF": security.csrf_token,
    }
    names = LocalizedRunNames.model_validate(
        {
            "en": "Inspect disposable repository",
            "zh-TW": "檢查拋棄式儲存庫",
            "zh-CN": "检查临时仓库",
        }
    )

    renamed = client.patch(
        "/api/v1/runs/disposable-run/names",
        headers=headers,
        json={"display_names": names.model_dump(mode="json", by_alias=True)},
    )
    preview = client.post(
        "/api/v1/runs/delete-preview",
        headers=headers,
        json={"run_ids": [run.run_id]},
    )
    deleted = client.post(
        "/api/v1/runs/delete",
        headers=headers,
        json={
            "run_ids": [run.run_id],
            "confirmation": "delete-selected-runs",
        },
    )

    assert renamed.status_code == 200
    assert renamed.json()["run"]["display_names"]["zh-TW"] == "檢查拋棄式儲存庫"
    assert preview.status_code == 200
    assert preview.json()["previews"][0]["deletable"] is True
    assert {item["kind"] for item in preview.json()["previews"][0]["artifacts"]} >= {
        "database",
        "worktree",
    }
    assert deleted.status_code == 200
    assert deleted.json()["results"] == [{"run_id": run.run_id, "deleted": True, "blockers": []}]
    with pytest.raises(RunNotFoundError):
        store.get_run(run.run_id)


def test_dashboard_lists_scoped_runs_and_usage(tmp_path: Path) -> None:
    workspace = repository(tmp_path)
    config = dashboard_config(tmp_path)
    store = StateStore(config.database_path)
    run = store.create_run(
        StartRunRequest(goal="Observe the Dashboard.", workspace=str(workspace)),
        run_id="dashboard-run",
        base_branch="main",
        base_commit=git(workspace, "rev-parse", "HEAD"),
    )
    store.record_provider_usage(
        run.run_id,
        ProviderUsageRecord(
            phase="planning",
            executor=Executor.CODEX,
            model="gpt-test",
            outcome=ProviderInvocationOutcome.SUCCEEDED,
            input_tokens=120,
            cached_input_tokens=100,
            output_tokens=30,
            reasoning_output_tokens=10,
        ),
    )
    client, security = dashboard_client(config, store, workspace)
    authenticate(client, security)

    workspaces = client.get("/api/v1/workspaces").json()["workspaces"]
    runs = client.get(
        "/api/v1/runs",
        params={"workspace": str(workspace), "limit": 50, "offset": 0},
    ).json()["runs"]
    detail = client.get("/api/v1/runs/dashboard-run").json()

    assert workspaces[0]["workspace"] == str(workspace)
    assert runs[0]["observed_total_tokens"] == 150
    assert detail["run"]["run_id"] == "dashboard-run"
    assert detail["usage"]["totals"]["observed_total_tokens"] == 150
    assert detail["actions"]["can_cancel"] is True


def test_dashboard_cancel_requires_same_origin_and_csrf(tmp_path: Path) -> None:
    workspace = repository(tmp_path)
    config = dashboard_config(tmp_path)
    store = StateStore(config.database_path)
    run = store.create_run(
        StartRunRequest(goal="Cancel safely.", workspace=str(workspace)),
        run_id="cancel-run",
    )
    store.begin_planning(run.run_id)
    client, security = dashboard_client(config, store, workspace)
    authenticate(client, security)

    endpoint = "/api/v1/runs/cancel-run/cancel"
    assert client.post(endpoint).status_code == 403
    assert client.post(endpoint, headers={"Origin": security.origin}).status_code == 403
    response = client.post(
        endpoint,
        headers={
            "Origin": security.origin,
            "X-AgentBraid-CSRF": security.csrf_token,
        },
    )

    assert response.status_code == 200
    assert response.json()["run"]["status"] == "cancelled"
    assert store.get_run(run.run_id).status == RunStatus.CANCELLED


def test_dashboard_apply_revalidates_reviewed_git_target(tmp_path: Path) -> None:
    workspace = repository(tmp_path)
    config = dashboard_config(tmp_path)
    store = StateStore(config.database_path)
    manager = WorktreeManager(workspace, config.worktree_dir)
    target = manager.primary_target()
    branch = "agentbraid/integration/dashboard"
    integration = manager.prepare_run("apply-run", branch, base_commit=target.commit)
    (integration.path / "dashboard.txt").write_text("reviewed\n", encoding="utf-8")
    git(integration.path, "add", "dashboard.txt")
    git(integration.path, "commit", "--signoff", "-m", "feat: reviewed dashboard file")

    run = store.create_run(
        StartRunRequest(goal="Apply reviewed work.", workspace=str(workspace)),
        run_id="apply-run",
        base_branch=target.branch,
        base_commit=target.commit,
    )
    store.begin_planning(run.run_id)
    plan, assignments = single_task_plan()
    store.save_plan(
        run.run_id,
        plan,
        assignments,
        integration_branch=branch,
        lead_thread_id="dashboard-thread",
    )
    store.set_run_status(run.run_id, RunStatus.INTEGRATING)
    store.set_run_status(run.run_id, RunStatus.REVIEWING)
    store.set_run_status(run.run_id, RunStatus.COMPLETED, final_summary="Approved.")
    client, security = dashboard_client(config, store, workspace)
    authenticate(client, security)
    headers = {
        "Origin": security.origin,
        "X-AgentBraid-CSRF": security.csrf_token,
    }

    readiness = client.get("/api/v1/runs/apply-run").json()["actions"]["apply"]
    assert readiness["can_apply"] is True
    rejected = client.post(
        "/api/v1/runs/apply-run/apply",
        headers=headers,
        json={"confirmation": "yes"},
    )
    assert rejected.status_code == 409
    applied = client.post(
        "/api/v1/runs/apply-run/apply",
        headers=headers,
        json={"confirmation": "apply-reviewed-run"},
    )

    assert applied.status_code == 200
    assert applied.json()["result"]["run_id"] == "apply-run"
    assert (workspace / "dashboard.txt").read_text(encoding="utf-8") == "reviewed\n"
