from __future__ import annotations

import asyncio
import json
import os
import secrets
import socket
import subprocess
import threading
import webbrowser
from collections import defaultdict
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, replace
from importlib.resources import files
from pathlib import Path
from typing import Literal, TypeAlias, TypeVar
from urllib.parse import urlencode

import uvicorn
from pydantic import ValidationError
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.routing import Route
from starlette.types import ASGIApp

from agentbraid import __version__
from agentbraid.config import AgentBraidConfig
from agentbraid.errors import (
    AgentBraidError,
    RunNotFoundError,
    SecurityBoundaryError,
    StateError,
    WorktreeError,
)
from agentbraid.models import (
    ApplyReadiness,
    Executor,
    LocalizedRunNames,
    ProviderUsageRecord,
    RunCleanupArtifact,
    RunCleanupPreview,
    RunCleanupResult,
    RunSnapshot,
    RunStatus,
    StartRunRequest,
    StrictModel,
    WorkspaceSettings,
    WorkspaceSummary,
    utc_now,
)
from agentbraid.security import assert_safe_runtime_paths, is_codex_binary
from agentbraid.service import AgentBraidService
from agentbraid.store import StateStore

_COOKIE_NAME = "agentbraid_dashboard"
_ACTIVE_RUN_STATUSES = frozenset(
    {
        RunStatus.CREATED,
        RunStatus.PLANNING,
        RunStatus.RUNNING,
        RunStatus.INTEGRATING,
        RunStatus.REVIEWING,
        RunStatus.BLOCKED,
    }
)
_SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    ),
    "Cross-Origin-Opener-Policy": "same-origin",
    "Referrer-Policy": "no-referrer",
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
}
_UsageTotals: TypeAlias = dict[str, int | float]
_RequestModel = TypeVar("_RequestModel", bound=StrictModel)
_ENVIRONMENT_SETTING_FIELDS = {
    "AGENTBRAID_CODEX_BINARY": "codex_binary",
    "AGENTBRAID_CODEX_MODEL": "codex_model",
    "AGENTBRAID_WORKTREE_DIR": "worktree_dir",
    "AGENTBRAID_CODEX_TIMEOUT_SECONDS": "codex_timeout_seconds",
    "AGENTBRAID_MAX_PARALLEL_CODEX": "max_parallel_codex",
    "AGENTBRAID_MAX_OUTPUT_BYTES": "max_output_bytes",
    "AGENTBRAID_MAX_TASK_ATTEMPTS": "max_task_attempts",
}


class _ApplyRequest(StrictModel):
    confirmation: Literal["apply-reviewed-run"]


class _DashboardStartRequest(StrictModel):
    request: StartRunRequest
    save_defaults: bool = False


class _RenameRequest(StrictModel):
    display_names: LocalizedRunNames


class _DeletePreviewRequest(StrictModel):
    run_ids: list[str]


class _DeleteRunsRequest(_DeletePreviewRequest):
    confirmation: Literal["delete-selected-runs"]


class _WorkspaceSettingsRequest(StrictModel):
    settings: WorkspaceSettings


@dataclass(slots=True)
class DashboardSecurity:
    origin: str
    allowed_hosts: frozenset[str]
    bootstrap_token: str
    session_token: str
    csrf_token: str
    bootstrap_used: bool = False

    @classmethod
    def create(cls, origin: str, allowed_hosts: frozenset[str]) -> DashboardSecurity:
        return cls(
            origin=origin,
            allowed_hosts=allowed_hosts,
            bootstrap_token=secrets.token_urlsafe(32),
            session_token=secrets.token_urlsafe(32),
            csrf_token=secrets.token_urlsafe(32),
        )

    def bootstrap_url(self) -> str:
        return f"{self.origin}/bootstrap?{urlencode({'token': self.bootstrap_token})}"

    def consume_bootstrap(self, candidate: str) -> bool:
        if self.bootstrap_used or not secrets.compare_digest(candidate, self.bootstrap_token):
            return False
        self.bootstrap_used = True
        return True

    def has_session(self, request: Request) -> bool:
        candidate = request.cookies.get(_COOKIE_NAME, "")
        return bool(candidate) and secrets.compare_digest(candidate, self.session_token)

    def authorize_mutation(self, request: Request) -> None:
        if request.headers.get("origin") != self.origin:
            raise SecurityBoundaryError("Dashboard mutation requires the same browser origin")
        candidate = request.headers.get("x-agentbraid-csrf", "")
        if not candidate or not secrets.compare_digest(candidate, self.csrf_token):
            raise SecurityBoundaryError("Dashboard mutation requires a valid CSRF token")


class _DashboardHeadersMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, *, allowed_hosts: frozenset[str]) -> None:
        super().__init__(app)
        self.allowed_hosts = allowed_hosts

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        if request.headers.get("host", "") not in self.allowed_hosts:
            response: Response = JSONResponse(
                {"error": {"code": "invalid_host", "message": "invalid Dashboard host"}},
                status_code=400,
            )
        else:
            response = await call_next(request)
        for name, value in _SECURITY_HEADERS.items():
            response.headers[name] = value
        return response


class DashboardController:
    def __init__(
        self,
        config: AgentBraidConfig,
        store: StateStore,
        *,
        initial_workspace: Path | None,
    ) -> None:
        self.config = config
        self.store = store
        self.initial_workspace = (
            str(initial_workspace.expanduser().resolve()) if initial_workspace is not None else None
        )
        self._background_runs: dict[str, tuple[AgentBraidService, asyncio.Task[None]]] = {}
        self._restart_required_workspaces: set[str] = set()
        self._active_runtime: dict[str, tuple[str, Path]] = {}

    def metadata(self) -> dict[str, object]:
        return {
            "version": __version__,
            "initial_workspace": self.initial_workspace,
            "database_scope": "active_state_database",
            "can_start_runs": True,
            "schema_version": 4,
        }

    def list_workspaces(self) -> list[dict[str, object]]:
        workspaces = self.store.list_workspaces()
        if self.initial_workspace and not any(
            item.workspace == self.initial_workspace for item in workspaces
        ):
            workspaces.insert(
                0,
                WorkspaceSummary(
                    workspace=self.initial_workspace,
                    updated_at=utc_now(),
                ),
            )
        return [item.model_dump(mode="json") for item in workspaces]

    def list_runs(self, workspace: str | None, limit: int, offset: int) -> list[dict[str, object]]:
        return [
            item.model_dump(mode="json", by_alias=True)
            for item in self.store.list_runs(workspace=workspace, limit=limit, offset=offset)
        ]

    def list_capabilities(self) -> list[dict[str, object]]:
        return [item.model_dump(mode="json") for item in self.store.list_capabilities()]

    def model_options(self) -> dict[str, object]:
        return {
            "codex": self.store.list_observed_models(Executor.CODEX),
            "host": self.store.list_observed_models(Executor.HOST),
            "codex_catalog_available": False,
            "host_controlled_by_dashboard": False,
        }

    def get_settings(self, workspace: str) -> dict[str, object]:
        path = self._workspace_path(workspace)
        config = self._base_config(path)
        saved = self.store.get_workspace_settings(str(path))
        settings = saved or config.default_workspace_settings(path)
        locked = _locked_setting_fields()
        settings = _apply_environment_locks(settings, config, locked)
        self._remember_active_runtime(path, config, saved)
        return {
            "settings": settings.model_dump(mode="json"),
            "database_path": str(self.store.database_path),
            "state_dir": str(config.state_dir),
            "locked_fields": locked,
            "requires_restart": str(path) in self._restart_required_workspaces,
        }

    def save_settings(self, settings: WorkspaceSettings) -> dict[str, object]:
        path = self._workspace_path(settings.workspace)
        base = self._base_config(path)
        saved = self.store.get_workspace_settings(str(path))
        active_binary, active_worktree = self._remember_active_runtime(path, base, saved)
        normalized = _apply_environment_locks(settings, base, _locked_setting_fields()).model_copy(
            update={"workspace": str(path), "updated_at": utc_now()}
        )
        if not is_codex_binary(normalized.codex_binary):
            raise StateError("codex_binary must name the official codex executable")
        candidate = base.with_workspace_runtime(normalized)
        assert_safe_runtime_paths(
            path,
            candidate.state_dir,
            self.store.database_path,
            candidate.worktree_dir,
        )
        saved = self.store.upsert_workspace_settings(normalized)
        if (
            candidate.codex_binary != active_binary
            or candidate.worktree_dir.resolve() != active_worktree
        ):
            self._restart_required_workspaces.add(str(path))
        else:
            self._restart_required_workspaces.discard(str(path))
        return self.get_settings(saved.workspace)

    async def start_run(self, payload: _DashboardStartRequest) -> dict[str, object]:
        if not payload.request.workspace:
            raise StateError("Dashboard start requires a workspace")
        workspace = self._workspace_path(payload.request.workspace)
        if str(workspace) in self._restart_required_workspaces:
            raise StateError("restart the Dashboard before starting with changed runtime settings")
        service = self._service_for_workspace(workspace)
        request = payload.request.model_copy(update={"workspace": str(workspace)})
        run = service.create_run(request)
        if payload.save_defaults:
            resolved = service.resolve_execution_settings(request)
            current = self.store.get_workspace_settings(str(workspace))
            self.store.upsert_workspace_settings(
                WorkspaceSettings(
                    **resolved.model_dump(),
                    workspace=str(workspace),
                    updated_at=current.updated_at if current is not None else utc_now(),
                )
            )
        task = asyncio.create_task(self._execute_background(service, run.run_id))
        self._background_runs[run.run_id] = (service, task)
        task.add_done_callback(lambda _: self._background_runs.pop(run.run_id, None))
        return run.model_dump(mode="json", by_alias=True)

    def rename_run(self, run_id: str, display_names: LocalizedRunNames) -> dict[str, object]:
        return self.store.update_run_names(run_id, display_names).model_dump(
            mode="json", by_alias=True
        )

    def preview_delete(self, run_ids: list[str]) -> list[dict[str, object]]:
        previews: list[RunCleanupPreview] = []
        for run_id in _normalized_run_ids(run_ids):
            try:
                run = self.store.get_run(run_id)
                preview = self._service_for_run(run).preview_run_cleanup(run_id)
            except (AgentBraidError, OSError) as exc:
                preview = RunCleanupPreview(
                    run_id=run_id,
                    deletable=False,
                    blockers=[_cleanup_error_message(exc)],
                    artifacts=[
                        RunCleanupArtifact(
                            kind="database",
                            identifier=run_id,
                            removable=False,
                            detail="record preserved because local cleanup could not be verified",
                        )
                    ],
                )
            previews.append(preview)
        return [item.model_dump(mode="json") for item in previews]

    def delete_runs(self, run_ids: list[str]) -> list[dict[str, object]]:
        results: list[RunCleanupResult] = []
        for run_id in _normalized_run_ids(run_ids):
            try:
                run = self.store.get_run(run_id)
                result = self._service_for_run(run).delete_run(run_id)
            except (AgentBraidError, OSError) as exc:
                result = RunCleanupResult(
                    run_id=run_id,
                    deleted=False,
                    blockers=[_cleanup_error_message(exc)],
                )
            results.append(result)
        return [item.model_dump(mode="json") for item in results]

    async def shutdown(self) -> None:
        active = list(self._background_runs.items())
        for run_id, (service, task) in active:
            if not task.done():
                with suppress(AgentBraidError):
                    service.cancel_run(run_id)
                task.cancel()
        if active:
            await asyncio.gather(*(task for _, (_, task) in active), return_exceptions=True)

    async def _execute_background(self, service: AgentBraidService, run_id: str) -> None:
        try:
            await service.execute_run(run_id)
        except asyncio.CancelledError:
            return
        except AgentBraidError as exc:
            self._mark_background_failure(run_id, exc.message)
        except Exception as exc:
            self._mark_background_failure(
                run_id,
                f"Dashboard run failed unexpectedly: {type(exc).__name__}",
            )

    def _mark_background_failure(self, run_id: str, message: str) -> None:
        with suppress(AgentBraidError):
            status = self.store.get_run_status(run_id)
            if status not in {
                RunStatus.BLOCKED,
                RunStatus.COMPLETED,
                RunStatus.CANCELLED,
                RunStatus.FAILED,
            }:
                self.store.set_run_status(run_id, RunStatus.FAILED, error=message)

    def get_run_detail(self, run_id: str) -> dict[str, object]:
        run = self.store.get_run(run_id)
        readiness = self._apply_readiness(run)
        return {
            "run": run.model_dump(mode="json", by_alias=True),
            "events": [
                event.model_dump(mode="json") for event in self.store.list_run_events(run_id)
            ],
            "usage": summarize_usage(run.provider_usage),
            "actions": {
                "can_cancel": run.status in _ACTIVE_RUN_STATUSES,
                "apply": readiness.model_dump(mode="json"),
            },
        }

    def cancel_run(self, run_id: str) -> dict[str, object]:
        return self.store.cancel_run(run_id).model_dump(mode="json", by_alias=True)

    def apply_run(self, run_id: str, confirmation: str) -> dict[str, object]:
        run = self.store.get_run(run_id)
        service = self._service_for_run(run)
        return service.apply_run(run_id, confirmation).model_dump(mode="json")

    def _apply_readiness(self, run: RunSnapshot) -> ApplyReadiness:
        try:
            return self._service_for_run(run).get_apply_readiness(run.run_id)
        except (AgentBraidError, OSError) as exc:
            message = (
                exc.message if isinstance(exc, AgentBraidError) else "workspace is unavailable"
            )
            return ApplyReadiness(
                can_apply=False,
                blockers=[message],
                expected_branch=run.base_branch,
                expected_commit=run.base_commit,
            )

    def _service_for_run(self, run: RunSnapshot) -> AgentBraidService:
        if not run.request.workspace:
            raise StateError("run does not identify its workspace")
        workspace = Path(run.request.workspace).expanduser().resolve()
        if not workspace.is_dir():
            raise WorktreeError("run workspace is unavailable", detail=str(workspace))
        if str(workspace) in self._restart_required_workspaces and run.execution_settings is None:
            raise StateError("restart the Dashboard before managing this legacy run")
        return self._service_for_workspace(workspace)

    def _service_for_workspace(self, workspace: Path) -> AgentBraidService:
        config = self._base_config(workspace)
        saved = self.store.get_workspace_settings(str(workspace))
        if saved is not None:
            config = config.with_workspace_runtime(saved)
        config.ensure_directories()
        return AgentBraidService(config, workspace, store=self.store)

    def _base_config(self, workspace: Path) -> AgentBraidConfig:
        if self.initial_workspace == str(workspace):
            return replace(self.config, database_path=self.store.database_path)
        return replace(
            AgentBraidConfig.load(workspace),
            database_path=self.store.database_path,
        )

    def _remember_active_runtime(
        self,
        workspace: Path,
        base: AgentBraidConfig,
        saved: WorkspaceSettings | None,
    ) -> tuple[str, Path]:
        key = str(workspace)
        if key not in self._active_runtime:
            active = base.with_workspace_runtime(saved) if saved is not None else base
            self._active_runtime[key] = (active.codex_binary, active.worktree_dir.resolve())
        return self._active_runtime[key]

    def _workspace_path(self, workspace: str) -> Path:
        resolved = Path(workspace).expanduser().resolve()
        known = {item.workspace for item in self.store.list_workspaces()}
        if self.initial_workspace is not None:
            known.add(self.initial_workspace)
        if str(resolved) not in known:
            raise SecurityBoundaryError("Dashboard workspace is outside the active database scope")
        if not resolved.is_dir() or _git_workspace(resolved) != resolved:
            raise WorktreeError("Dashboard workspace is unavailable", detail=str(resolved))
        return resolved


def summarize_usage(records: list[ProviderUsageRecord]) -> dict[str, object]:
    final_attempts: dict[str, int] = {}
    for record in records:
        if record.task_id is not None and record.attempt is not None:
            final_attempts[record.task_id] = max(
                final_attempts.get(record.task_id, 0), record.attempt
            )

    totals = _empty_usage_totals()
    by_phase: dict[str, _UsageTotals] = defaultdict(_empty_usage_totals)
    by_model: dict[str, _UsageTotals] = defaultdict(_empty_usage_totals)
    retry_tokens = 0
    legacy_count = 0
    serialized_records: list[dict[str, object]] = []
    for record in records:
        metrics = _usage_metrics(record)
        _add_usage(totals, metrics)
        _add_usage(by_phase[record.phase], metrics)
        _add_usage(by_model[record.model], metrics)
        if record.outcome is None or (record.phase == "task" and record.attempt is None):
            legacy_count += 1
        if (
            record.task_id is not None
            and record.attempt is not None
            and record.attempt < final_attempts[record.task_id]
        ):
            retry_tokens += int(metrics["observed_total_tokens"])
        serialized_records.append(record.model_dump(mode="json"))
    totals["retry_tokens"] = retry_tokens
    totals["legacy_invocation_count"] = legacy_count
    return {
        "totals": totals,
        "by_phase": _named_usage_buckets(by_phase, "phase"),
        "by_model": _named_usage_buckets(by_model, "model"),
        "records": serialized_records,
    }


def _empty_usage_totals() -> _UsageTotals:
    return {
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "uncached_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_output_tokens": 0,
        "non_reasoning_output_tokens": 0,
        "observed_total_tokens": 0,
        "duration_seconds": 0.0,
        "invocation_count": 0,
    }


def _usage_metrics(record: ProviderUsageRecord) -> _UsageTotals:
    cached = min(record.cached_input_tokens, record.input_tokens)
    reasoning = min(record.reasoning_output_tokens, record.output_tokens)
    return {
        "input_tokens": record.input_tokens,
        "cached_input_tokens": cached,
        "uncached_input_tokens": record.input_tokens - cached,
        "output_tokens": record.output_tokens,
        "reasoning_output_tokens": reasoning,
        "non_reasoning_output_tokens": record.output_tokens - reasoning,
        "observed_total_tokens": record.input_tokens + record.output_tokens,
        "duration_seconds": record.duration_seconds,
        "invocation_count": 1,
    }


def _add_usage(target: _UsageTotals, metrics: _UsageTotals) -> None:
    for name in metrics:
        if name == "duration_seconds":
            target[name] = float(target[name]) + float(metrics[name])
        else:
            target[name] = int(target[name]) + int(metrics[name])


def _named_usage_buckets(
    buckets: dict[str, _UsageTotals],
    key_name: str,
) -> list[dict[str, object]]:
    return [
        {key_name: name, **values}
        for name, values in sorted(buckets.items(), key=lambda item: item[0])
    ]


def create_dashboard_app(
    controller: DashboardController,
    security: DashboardSecurity,
) -> Starlette:
    async def bootstrap(request: Request) -> Response:
        candidate = request.query_params.get("token", "")
        if not security.consume_bootstrap(candidate):
            return _error_response(
                "invalid_bootstrap",
                "Dashboard bootstrap token is invalid or already used",
                403,
            )
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(
            _COOKIE_NAME,
            security.session_token,
            httponly=True,
            samesite="strict",
            secure=False,
            path="/",
        )
        return response

    async def index(request: Request) -> Response:
        _require_session(request, security)
        content = _asset_text("index.html").replace("__CSRF_TOKEN__", security.csrf_token)
        return HTMLResponse(content)

    async def asset(request: Request) -> Response:
        _require_session(request, security)
        name = request.path_params["name"]
        if name not in {"app.css", "app.js", "locales.json"}:
            return _error_response("asset_not_found", "Dashboard asset not found", 404)
        media_types = {
            "app.css": "text/css",
            "app.js": "text/javascript",
            "locales.json": "application/json",
        }
        media_type = media_types[name]
        return Response(_asset_text(name), media_type=media_type)

    async def metadata(request: Request) -> Response:
        _require_session(request, security)
        return JSONResponse(controller.metadata())

    async def workspaces(request: Request) -> Response:
        _require_session(request, security)
        return JSONResponse({"workspaces": controller.list_workspaces()})

    async def runs(request: Request) -> Response:
        _require_session(request, security)
        workspace = request.query_params.get("workspace") or None
        limit = _query_int(request, "limit", default=50)
        offset = _query_int(request, "offset", default=0)
        return JSONResponse(
            {
                "runs": controller.list_runs(workspace, limit, offset),
                "limit": limit,
                "offset": offset,
            }
        )

    async def start_run(request: Request) -> Response:
        _require_session(request, security)
        security.authorize_mutation(request)
        body = await _validated_body(request, _DashboardStartRequest)
        return JSONResponse({"run": await controller.start_run(body)}, status_code=202)

    async def run_detail(request: Request) -> Response:
        _require_session(request, security)
        return JSONResponse(controller.get_run_detail(request.path_params["run_id"]))

    async def capabilities(request: Request) -> Response:
        _require_session(request, security)
        return JSONResponse({"capabilities": controller.list_capabilities()})

    async def model_options(request: Request) -> Response:
        _require_session(request, security)
        return JSONResponse(controller.model_options())

    async def settings(request: Request) -> Response:
        _require_session(request, security)
        workspace = request.query_params.get("workspace", "")
        if not workspace:
            raise StateError("settings request requires a workspace")
        return JSONResponse(controller.get_settings(workspace))

    async def save_settings(request: Request) -> Response:
        _require_session(request, security)
        security.authorize_mutation(request)
        body = await _validated_body(request, _WorkspaceSettingsRequest)
        return JSONResponse(controller.save_settings(body.settings))

    async def cancel_run(request: Request) -> Response:
        _require_session(request, security)
        security.authorize_mutation(request)
        return JSONResponse({"run": controller.cancel_run(request.path_params["run_id"])})

    async def apply_run(request: Request) -> Response:
        _require_session(request, security)
        security.authorize_mutation(request)
        try:
            body = _ApplyRequest.model_validate(await request.json())
        except (json.JSONDecodeError, ValidationError) as exc:
            raise StateError("apply request must contain the exact confirmation phrase") from exc
        return JSONResponse(
            {
                "result": controller.apply_run(
                    request.path_params["run_id"],
                    body.confirmation,
                )
            }
        )

    async def rename_run(request: Request) -> Response:
        _require_session(request, security)
        security.authorize_mutation(request)
        body = await _validated_body(request, _RenameRequest)
        return JSONResponse(
            {
                "run": controller.rename_run(
                    request.path_params["run_id"],
                    body.display_names,
                )
            }
        )

    async def delete_preview(request: Request) -> Response:
        _require_session(request, security)
        security.authorize_mutation(request)
        body = await _validated_body(request, _DeletePreviewRequest)
        return JSONResponse({"previews": controller.preview_delete(body.run_ids)})

    async def delete_runs(request: Request) -> Response:
        _require_session(request, security)
        security.authorize_mutation(request)
        body = await _validated_body(request, _DeleteRunsRequest)
        return JSONResponse({"results": controller.delete_runs(body.run_ids)})

    routes = [
        Route("/bootstrap", bootstrap, methods=["GET"]),
        Route("/", index, methods=["GET"]),
        Route("/assets/{name:str}", asset, methods=["GET"]),
        Route("/api/v1/meta", metadata, methods=["GET"]),
        Route("/api/v1/workspaces", workspaces, methods=["GET"]),
        Route("/api/v1/runs", runs, methods=["GET"]),
        Route("/api/v1/runs", start_run, methods=["POST"]),
        Route("/api/v1/capabilities", capabilities, methods=["GET"]),
        Route("/api/v1/model-options", model_options, methods=["GET"]),
        Route("/api/v1/settings", settings, methods=["GET"]),
        Route("/api/v1/settings", save_settings, methods=["PUT"]),
        Route("/api/v1/runs/delete-preview", delete_preview, methods=["POST"]),
        Route("/api/v1/runs/delete", delete_runs, methods=["POST"]),
        Route("/api/v1/runs/{run_id:str}", run_detail, methods=["GET"]),
        Route("/api/v1/runs/{run_id:str}/cancel", cancel_run, methods=["POST"]),
        Route("/api/v1/runs/{run_id:str}/apply", apply_run, methods=["POST"]),
        Route("/api/v1/runs/{run_id:str}/names", rename_run, methods=["PATCH"]),
    ]
    middleware = [
        Middleware(_DashboardHeadersMiddleware, allowed_hosts=security.allowed_hosts),
    ]

    @asynccontextmanager
    async def lifespan(application: Starlette) -> AsyncIterator[None]:
        del application
        yield
        await controller.shutdown()

    return Starlette(
        routes=routes,
        middleware=middleware,
        lifespan=lifespan,
        exception_handlers={AgentBraidError: _agentbraid_error_handler},
    )


def run_dashboard(path: Path, *, port: int = 0, open_browser: bool = True) -> None:
    if not 0 <= port <= 65535:
        raise StateError("Dashboard port must be between 0 and 65535")
    resolved = path.expanduser().resolve()
    if not resolved.is_dir():
        raise StateError("Dashboard path must be an existing directory", detail=str(resolved))
    initial_workspace = _git_workspace(resolved)
    config = AgentBraidConfig.load(initial_workspace)
    config.ensure_directories()
    controller = DashboardController(
        config,
        StateStore(config.database_path),
        initial_workspace=initial_workspace,
    )

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        listener.bind(("127.0.0.1", port))
    except OSError as exc:
        listener.close()
        raise StateError("could not bind the local Dashboard port", detail=str(exc)) from exc
    listener.listen(128)
    selected_port = int(listener.getsockname()[1])
    origin = f"http://127.0.0.1:{selected_port}"
    security = DashboardSecurity.create(origin, frozenset({f"127.0.0.1:{selected_port}"}))
    application = create_dashboard_app(controller, security)
    bootstrap_url = security.bootstrap_url()
    print(f"AgentBraid Dashboard: {bootstrap_url}")
    print("Press Ctrl+C to stop the local Dashboard.")
    if open_browser:
        threading.Timer(0.2, webbrowser.open, args=(bootstrap_url,)).start()
    server = uvicorn.Server(
        uvicorn.Config(
            application,
            host="127.0.0.1",
            port=selected_port,
            access_log=False,
            log_level="warning",
        )
    )
    try:
        server.run(sockets=[listener])
    except KeyboardInterrupt:
        pass
    finally:
        listener.close()


async def _agentbraid_error_handler(request: Request, exc: Exception) -> JSONResponse:
    del request
    if not isinstance(exc, AgentBraidError):
        return _error_response("dashboard_error", "Dashboard request failed", 500)
    if isinstance(exc, RunNotFoundError):
        status_code = 404
    elif isinstance(exc, SecurityBoundaryError):
        status_code = 403
    elif isinstance(exc, (StateError, WorktreeError)):
        status_code = 409
    else:
        status_code = 400
    return JSONResponse({"error": exc.as_dict()}, status_code=status_code)


def _require_session(request: Request, security: DashboardSecurity) -> None:
    if not security.has_session(request):
        raise SecurityBoundaryError("Dashboard session is not authorized")


def _query_int(request: Request, name: str, *, default: int) -> int:
    value = request.query_params.get(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError as exc:
        raise StateError(f"Dashboard query parameter must be an integer: {name}") from exc


async def _validated_body(
    request: Request,
    model: type[_RequestModel],
) -> _RequestModel:
    try:
        return model.model_validate(await request.json())
    except (json.JSONDecodeError, ValidationError) as exc:
        raise StateError("Dashboard request body is invalid", detail=str(exc)) from exc


def _locked_setting_fields() -> list[str]:
    return [
        field
        for environment, field in _ENVIRONMENT_SETTING_FIELDS.items()
        if environment in os.environ
    ]


def _apply_environment_locks(
    settings: WorkspaceSettings,
    config: AgentBraidConfig,
    locked_fields: list[str],
) -> WorkspaceSettings:
    defaults = config.default_workspace_settings(Path(settings.workspace))
    config_values: dict[str, object] = {
        "codex_binary": config.codex_binary,
        "codex_model": config.codex_model,
        "worktree_dir": str(config.worktree_dir.expanduser().resolve()),
        "codex_timeout_seconds": defaults.codex_timeout_seconds,
        "max_parallel_codex": defaults.max_parallel_codex,
        "max_output_bytes": defaults.max_output_bytes,
        "max_task_attempts": defaults.max_task_attempts,
    }
    return settings.model_copy(update={field: config_values[field] for field in locked_fields})


def _cleanup_error_message(error: AgentBraidError | OSError) -> str:
    if isinstance(error, AgentBraidError):
        return f"{error.message}: {error.detail}" if error.detail else error.message
    return str(error)


def _normalized_run_ids(run_ids: list[str]) -> list[str]:
    normalized = list(dict.fromkeys(item.strip() for item in run_ids if item.strip()))
    if not normalized:
        raise StateError("at least one run must be selected")
    if len(normalized) > 100:
        raise StateError("no more than 100 runs may be selected")
    return normalized


def _asset_text(name: str) -> str:
    return files("agentbraid.dashboard_assets").joinpath(name).read_text(encoding="utf-8")


def _error_response(code: str, message: str, status_code: int) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status_code)


def _git_workspace(path: Path) -> Path | None:
    try:
        completed = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            capture_output=True,
            check=False,
            text=True,
            timeout=10,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    return Path(completed.stdout.strip()).resolve()
