from __future__ import annotations

import json
import secrets
import socket
import subprocess
import threading
import webbrowser
from collections import defaultdict
from dataclasses import dataclass, replace
from importlib.resources import files
from pathlib import Path
from typing import Literal, TypeAlias
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
    ProviderUsageRecord,
    RunPlan,
    RunReview,
    RunSnapshot,
    RunStatus,
    StartRunRequest,
    StrictModel,
    TaskSpec,
    WorkerResult,
)
from agentbraid.providers.base import StructuredProviderResult
from agentbraid.service import AgentBraidService, CodexProvider
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


class _ApplyRequest(StrictModel):
    confirmation: Literal["apply-reviewed-run"]


class _DashboardCodexProvider(CodexProvider):
    async def plan(
        self,
        request: StartRunRequest,
        workspace: Path,
    ) -> StructuredProviderResult[RunPlan]:
        raise StateError("the Dashboard cannot start AgentBraid runs")

    async def execute_task(
        self,
        task: TaskSpec,
        workspace: Path,
        *,
        run_id: str,
    ) -> StructuredProviderResult[WorkerResult]:
        raise StateError("the Dashboard cannot execute AgentBraid tasks")

    async def review_run(
        self,
        run: RunSnapshot,
        workspace: Path,
    ) -> StructuredProviderResult[RunReview]:
        raise StateError("the Dashboard cannot review AgentBraid runs")


_DASHBOARD_CODEX = _DashboardCodexProvider()


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

    def metadata(self) -> dict[str, object]:
        return {
            "version": __version__,
            "initial_workspace": self.initial_workspace,
            "database_scope": "active_state_database",
        }

    def list_workspaces(self) -> list[dict[str, object]]:
        return [item.model_dump(mode="json") for item in self.store.list_workspaces()]

    def list_runs(self, workspace: str | None, limit: int, offset: int) -> list[dict[str, object]]:
        return [
            item.model_dump(mode="json")
            for item in self.store.list_runs(workspace=workspace, limit=limit, offset=offset)
        ]

    def list_capabilities(self) -> list[dict[str, object]]:
        return [item.model_dump(mode="json") for item in self.store.list_capabilities()]

    def get_run_detail(self, run_id: str) -> dict[str, object]:
        run = self.store.get_run(run_id)
        readiness = self._apply_readiness(run)
        return {
            "run": run.model_dump(mode="json"),
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
        return self.store.cancel_run(run_id).model_dump(mode="json")

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
        run_config = AgentBraidConfig.load(workspace)
        run_config = replace(run_config, database_path=self.store.database_path)
        return AgentBraidService(
            run_config,
            workspace,
            store=self.store,
            codex=_DASHBOARD_CODEX,
        )


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
        if name not in {"app.css", "app.js"}:
            return _error_response("asset_not_found", "Dashboard asset not found", 404)
        media_type = "text/css" if name.endswith(".css") else "text/javascript"
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

    async def run_detail(request: Request) -> Response:
        _require_session(request, security)
        return JSONResponse(controller.get_run_detail(request.path_params["run_id"]))

    async def capabilities(request: Request) -> Response:
        _require_session(request, security)
        return JSONResponse({"capabilities": controller.list_capabilities()})

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

    routes = [
        Route("/bootstrap", bootstrap, methods=["GET"]),
        Route("/", index, methods=["GET"]),
        Route("/assets/{name:str}", asset, methods=["GET"]),
        Route("/api/v1/meta", metadata, methods=["GET"]),
        Route("/api/v1/workspaces", workspaces, methods=["GET"]),
        Route("/api/v1/runs", runs, methods=["GET"]),
        Route("/api/v1/runs/{run_id:str}", run_detail, methods=["GET"]),
        Route("/api/v1/capabilities", capabilities, methods=["GET"]),
        Route("/api/v1/runs/{run_id:str}/cancel", cancel_run, methods=["POST"]),
        Route("/api/v1/runs/{run_id:str}/apply", apply_run, methods=["POST"]),
    ]
    middleware = [
        Middleware(_DashboardHeadersMiddleware, allowed_hosts=security.allowed_hosts),
    ]
    return Starlette(
        routes=routes,
        middleware=middleware,
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
