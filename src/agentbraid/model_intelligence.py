from __future__ import annotations

import json
import math
import os
import re
import shlex
import subprocess
import tempfile
from collections import defaultdict
from collections.abc import Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from importlib.resources import files
from pathlib import Path
from typing import IO, Any, Literal, Protocol, cast
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener

from pydantic import Field, ValidationError, field_validator, model_validator

from agentbraid.errors import StateError
from agentbraid.models import (
    CodexReasoningEffort,
    Executor,
    StrictModel,
    WorkloadComplexity,
)

CATALOG_TIMEOUT_SECONDS = 10
CATALOG_OUTPUT_LIMIT = 1024 * 1024
EXTERNAL_MANIFEST_URL = (
    "https://raw.githubusercontent.com/xuu33030/agentbraid/"
    "main/src/agentbraid/model_data/manifest-v1.json"
)
_DOWNLOAD_HOSTS = frozenset({"raw.githubusercontent.com"})
_SOURCE_HOSTS = frozenset(
    {
        "artificialanalysis.ai",
        "github.com",
        "huggingface.co",
        "raw.githubusercontent.com",
        "swebench.com",
        "terminal-bench.com",
        "www.swebench.com",
        "www.tbench.ai",
    }
)
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_MODEL_PREFIXES = ("claude", "gemini", "gpt", "o1", "o3", "o4")
_EFFORT_ORDER = {
    CodexReasoningEffort.LOW: 0,
    CodexReasoningEffort.MEDIUM: 1,
    CodexReasoningEffort.HIGH: 2,
    CodexReasoningEffort.XHIGH: 3,
    CodexReasoningEffort.MAX: 4,
    CodexReasoningEffort.ULTRA: 5,
}
_TARGET_EFFORT = {
    WorkloadComplexity.QUICK: CodexReasoningEffort.LOW,
    WorkloadComplexity.STANDARD: CodexReasoningEffort.MEDIUM,
    WorkloadComplexity.COMPLEX: CodexReasoningEffort.HIGH,
    WorkloadComplexity.HIGH_RISK: CodexReasoningEffort.XHIGH,
}
_CATEGORY_WEIGHT = {
    "agentic_coding": 1.0,
    "terminal_coding": 0.8,
    "repository_qa": 0.6,
}
_SUCCESS_OUTCOMES = frozenset({"succeeded", "approved"})
_FAILURE_OUTCOMES = frozenset({"failed", "blocked", "rejected"})


class CatalogModel(StrictModel):
    model: str = Field(min_length=1, max_length=200)
    display_name: str = Field(min_length=1, max_length=200)
    harness: Literal["codex", "agy"]
    supported_efforts: list[CodexReasoningEffort] = Field(default_factory=list)
    default_effort: CodexReasoningEffort | None = None
    variant_effort: CodexReasoningEffort | None = None
    source: Literal["codex_cli", "codex_bundled", "agy_cli", "observed"]
    position: int = Field(ge=0)

    @field_validator("model", "display_name")
    @classmethod
    def reject_control_characters(cls, value: str) -> str:
        return _safe_text(value)


class CatalogSnapshot(StrictModel):
    harness: Literal["codex", "agy"]
    models: list[CatalogModel] = Field(default_factory=list)
    status: Literal["ready", "fallback", "not_refreshed", "unavailable"]
    source: str
    detail: str | None = Field(default=None, max_length=1000)
    refreshed_at: datetime | None = None


class ManifestSource(StrictModel):
    source_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    name: str = Field(min_length=1, max_length=200)
    url: str = Field(min_length=1, max_length=2000)
    license_status: Literal["redistributable", "reference_only", "unknown"]
    notes: str = Field(default="", max_length=2000)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        _validate_https_url(value, _SOURCE_HOSTS)
        return value

    @model_validator(mode="after")
    def enforce_reference_only_sources(self) -> ManifestSource:
        if (
            urlparse(self.url).hostname == "artificialanalysis.ai"
            and self.license_status != "reference_only"
        ):
            raise ValueError("Artificial Analysis must remain reference-only")
        return self


class BenchmarkEntry(StrictModel):
    evidence_id: str = Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,127}$")
    source_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{0,62}$")
    model: str = Field(min_length=1, max_length=200)
    effort: CodexReasoningEffort | None = None
    harness: Literal["codex", "agy"]
    benchmark: str = Field(min_length=1, max_length=200)
    category: Literal["agentic_coding", "terminal_coding", "repository_qa"]
    raw_score: float
    scale_min: float
    scale_max: float
    higher_is_better: bool = True
    source_date: date
    version: str = Field(min_length=1, max_length=100)
    source_url: str = Field(min_length=1, max_length=2000)
    license_status: Literal["redistributable", "reference_only", "unknown"]
    model_match_confidence: Literal["exact", "alias", "approximate", "unmatched"]

    @field_validator("model", "benchmark", "version")
    @classmethod
    def validate_text(cls, value: str) -> str:
        return _safe_text(value)

    @field_validator("raw_score", "scale_min", "scale_max")
    @classmethod
    def validate_finite_number(cls, value: float) -> float:
        if not math.isfinite(value):
            raise ValueError("benchmark values must be finite")
        return value

    @field_validator("source_url")
    @classmethod
    def validate_source_url(cls, value: str) -> str:
        _validate_https_url(value, _SOURCE_HOSTS)
        return value

    @model_validator(mode="after")
    def validate_scale(self) -> BenchmarkEntry:
        if self.scale_max <= self.scale_min:
            raise ValueError("scale_max must be greater than scale_min")
        if not self.scale_min <= self.raw_score <= self.scale_max:
            raise ValueError("raw_score must be inside the declared scale")
        if self.source_date > date.today():
            raise ValueError("source_date must not be in the future")
        if (
            urlparse(self.source_url).hostname == "artificialanalysis.ai"
            and self.license_status != "reference_only"
        ):
            raise ValueError("Artificial Analysis must remain reference-only")
        return self


class ModelManifest(StrictModel):
    schema_version: Literal["1"]
    manifest_version: str = Field(min_length=1, max_length=100)
    generated_at: date
    sources: list[ManifestSource]
    entries: list[BenchmarkEntry]

    @model_validator(mode="after")
    def validate_manifest(self) -> ModelManifest:
        if self.generated_at > date.today():
            raise ValueError("generated_at must not be in the future")
        source_ids = [source.source_id for source in self.sources]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError("manifest source IDs must be unique")
        evidence_ids = [entry.evidence_id for entry in self.entries]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("manifest evidence IDs must be unique")
        known_sources = set(source_ids)
        if any(entry.source_id not in known_sources for entry in self.entries):
            raise ValueError("manifest entry references an unknown source")
        source_licenses = {source.source_id: source.license_status for source in self.sources}
        if any(
            source_licenses[entry.source_id] != "redistributable"
            and entry.license_status == "redistributable"
            for entry in self.entries
        ):
            raise ValueError("entries cannot exceed their source redistribution status")
        return self


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: bytes
    stderr: bytes = b""


class CatalogRunner(Protocol):
    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout: int,
        max_output_bytes: int,
    ) -> CommandResult: ...


class ManifestDownloader(Protocol):
    def download(self, url: str, *, timeout: int, max_bytes: int) -> bytes: ...


class SubprocessCatalogRunner:
    def run(
        self,
        argv: Sequence[str],
        *,
        cwd: Path,
        timeout: int,
        max_output_bytes: int,
    ) -> CommandResult:
        try:
            process = subprocess.Popen(
                list(argv),
                cwd=cwd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
            )
        except (FileNotFoundError, OSError) as exc:
            raise _CatalogFailure(f"executable unavailable: {argv[0]}") from exc
        try:
            if process.stdout is None or process.stderr is None:
                raise _CatalogFailure("catalog subprocess pipes were not created")
            with ThreadPoolExecutor(max_workers=2) as executor:
                stdout_future = executor.submit(
                    _read_bounded_stream,
                    process.stdout,
                    max_output_bytes,
                    process,
                )
                stderr_future = executor.submit(
                    _read_bounded_stream,
                    process.stderr,
                    max_output_bytes,
                    process,
                )
                try:
                    process.wait(timeout=timeout)
                except subprocess.TimeoutExpired as exc:
                    process.kill()
                    process.wait()
                    stdout_future.result()
                    stderr_future.result()
                    raise _CatalogFailure(f"command timed out after {timeout}s") from exc
                stdout = stdout_future.result()
                stderr = stderr_future.result()
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.wait()
            raise _CatalogFailure(f"command timed out after {timeout}s") from exc
        finally:
            if process.stdout is not None:
                process.stdout.close()
            if process.stderr is not None:
                process.stderr.close()
        if len(stdout) + len(stderr) > max_output_bytes:
            raise _CatalogFailure(f"command output exceeded {max_output_bytes} bytes")
        return CommandResult(process.returncode, stdout, stderr)


class _AllowlistedRedirectHandler(HTTPRedirectHandler):
    def redirect_request(
        self,
        request: Request,
        file_pointer: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> Request | None:
        _validate_https_url(new_url, _DOWNLOAD_HOSTS)
        return super().redirect_request(
            request,
            file_pointer,
            code,
            message,
            headers,
            new_url,
        )


class HttpsManifestDownloader:
    def download(self, url: str, *, timeout: int, max_bytes: int) -> bytes:
        _validate_https_url(url, _DOWNLOAD_HOSTS)
        request = Request(url, headers={"Accept": "application/json", "User-Agent": "AgentBraid"})
        opener = build_opener(_AllowlistedRedirectHandler())
        try:
            with opener.open(request, timeout=timeout) as response:
                final_url = str(response.geturl())
                _validate_https_url(final_url, _DOWNLOAD_HOSTS)
                content_length = response.headers.get("Content-Length")
                if content_length is not None and int(content_length) > max_bytes:
                    raise StateError("external model manifest exceeded the download limit")
                payload = cast(bytes, response.read(max_bytes + 1))
        except StateError:
            raise
        except (OSError, ValueError) as exc:
            raise StateError(
                "could not download the external model manifest",
                detail=str(exc),
            ) from exc
        if len(payload) > max_bytes:
            raise StateError("external model manifest exceeded the download limit")
        return payload


class _CatalogFailure(Exception):
    pass


class ModelIntelligence:
    def __init__(
        self,
        state_dir: Path,
        *,
        runner: CatalogRunner | None = None,
        downloader: ManifestDownloader | None = None,
    ) -> None:
        self.state_dir = state_dir.expanduser().resolve()
        self.runner = runner or SubprocessCatalogRunner()
        self.downloader = downloader or HttpsManifestDownloader()
        self._catalogs: dict[str, CatalogSnapshot] = {}

    def catalog_options(
        self,
        observed_codex: Sequence[str],
        observed_host: Sequence[str],
    ) -> dict[str, object]:
        codex = self._catalogs.get("codex") or _observed_snapshot("codex", observed_codex)
        agy = self._catalogs.get("agy") or _observed_snapshot("agy", observed_host)
        codex = _with_observed_models(codex, observed_codex)
        agy = _with_observed_models(agy, observed_host)
        manifest, manifest_origin = self.load_manifest()
        return {
            "codex": [model.model for model in codex.models],
            "host": [model.model for model in agy.models],
            "codex_catalog_available": codex.status == "ready",
            "host_controlled_by_dashboard": False,
            "catalogs": {
                "codex": codex.model_dump(mode="json"),
                "agy": agy.model_dump(mode="json"),
            },
            "reasoning_efforts": [effort.value for effort in CodexReasoningEffort],
            "intelligence": _manifest_details(manifest, manifest_origin),
        }

    def refresh_catalogs(
        self,
        workspace: Path,
        *,
        codex_binary: str,
        observed_codex: Sequence[str],
        observed_host: Sequence[str],
    ) -> dict[str, object]:
        codex_catalog = self._catalogs.get("codex")
        if codex_catalog is None or codex_catalog.status == "unavailable":
            self._catalogs["codex"] = self._load_codex_catalog(workspace, codex_binary)
        agy_catalog = self._catalogs.get("agy")
        if agy_catalog is None or agy_catalog.status == "unavailable":
            self._catalogs["agy"] = self._load_agy_catalog(workspace)
        return self.catalog_options(observed_codex, observed_host)

    def refresh_external_manifest(self) -> ModelManifest:
        payload = self.downloader.download(
            EXTERNAL_MANIFEST_URL,
            timeout=CATALOG_TIMEOUT_SECONDS,
            max_bytes=CATALOG_OUTPUT_LIMIT,
        )
        manifest = _validate_manifest(payload)
        if manifest.generated_at < date.today() - timedelta(days=730):
            raise StateError("external model manifest is stale")
        current, _ = self.load_manifest()
        if manifest.generated_at < current.generated_at:
            raise StateError("external model manifest is older than the local manifest")
        cache_path = self._manifest_cache_path()
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                dir=cache_path.parent,
                prefix="manifest-v1-",
                suffix=".tmp",
                delete=False,
            ) as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
                temporary_path = Path(handle.name)
            os.replace(temporary_path, cache_path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()
        return manifest

    def load_manifest(self) -> tuple[ModelManifest, Literal["cache", "packaged"]]:
        cache_path = self._manifest_cache_path()
        if cache_path.is_file():
            try:
                return _validate_manifest(cache_path.read_bytes()), "cache"
            except (OSError, StateError):
                pass
        packaged = files("agentbraid.model_data").joinpath("manifest-v1.json").read_bytes()
        return _validate_manifest(packaged), "packaged"

    def recommend(
        self,
        complexity: WorkloadComplexity,
        statistics: Mapping[Executor, Sequence[Mapping[str, object]]],
        observed_codex: Sequence[str],
        observed_host: Sequence[str],
    ) -> dict[str, object]:
        options = self.catalog_options(observed_codex, observed_host)
        catalogs = options["catalogs"]
        if not isinstance(catalogs, dict):
            raise StateError("model catalog state is invalid")
        manifest, origin = self.load_manifest()
        target_effort = _TARGET_EFFORT[complexity]
        recommendations: dict[str, list[dict[str, object]]] = {}
        for harness, executor in (("codex", Executor.CODEX), ("agy", Executor.HOST)):
            catalog = CatalogSnapshot.model_validate(catalogs[harness])
            recommendations[harness] = _rank_models(
                catalog.models,
                harness=harness,
                target_effort=target_effort,
                manifest=manifest,
                statistics=statistics.get(executor, ()),
            )[:3]
        return {
            "complexity": complexity.value,
            "target_effort": target_effort.value,
            "codex": recommendations["codex"],
            "agy": recommendations["agy"],
            "manifest_version": manifest.manifest_version,
            "manifest_origin": origin,
        }

    def guide(self, workspace: Path, agy_model: str) -> dict[str, object]:
        return build_guide(workspace, agy_model)

    def _load_codex_catalog(self, workspace: Path, codex_binary: str) -> CatalogSnapshot:
        errors: list[str] = []
        for bundled in (False, True):
            argv = [codex_binary, "debug", "models"]
            if bundled:
                argv.append("--bundled")
            try:
                result = self.runner.run(
                    argv,
                    cwd=workspace,
                    timeout=CATALOG_TIMEOUT_SECONDS,
                    max_output_bytes=CATALOG_OUTPUT_LIMIT,
                )
                _assert_bounded_command_result(result)
                if result.returncode != 0:
                    raise _CatalogFailure(_command_error(result))
                models = parse_codex_catalog(result.stdout, bundled=bundled)
                if not models:
                    raise _CatalogFailure("catalog did not contain visible models")
                return CatalogSnapshot(
                    harness="codex",
                    models=models,
                    status="ready",
                    source="codex_bundled" if bundled else "codex_cli",
                    refreshed_at=datetime.now(UTC),
                )
            except (
                _CatalogFailure,
                FileNotFoundError,
                OSError,
                subprocess.TimeoutExpired,
                ValueError,
                json.JSONDecodeError,
            ) as exc:
                errors.append(str(exc))
        return CatalogSnapshot(
            harness="codex",
            status="unavailable",
            source="observed",
            detail="; ".join(errors)[-1000:] or "Codex catalog unavailable",
            refreshed_at=datetime.now(UTC),
        )

    def _load_agy_catalog(self, workspace: Path) -> CatalogSnapshot:
        try:
            result = self.runner.run(
                ["agy", "models"],
                cwd=workspace,
                timeout=CATALOG_TIMEOUT_SECONDS,
                max_output_bytes=CATALOG_OUTPUT_LIMIT,
            )
            _assert_bounded_command_result(result)
            if result.returncode != 0:
                raise _CatalogFailure(_command_error(result))
            models = parse_agy_catalog(result.stdout)
            if not models:
                raise _CatalogFailure("catalog did not contain models")
            return CatalogSnapshot(
                harness="agy",
                models=models,
                status="ready",
                source="agy_cli",
                refreshed_at=datetime.now(UTC),
            )
        except (
            _CatalogFailure,
            FileNotFoundError,
            OSError,
            subprocess.TimeoutExpired,
            ValueError,
            json.JSONDecodeError,
        ) as exc:
            return CatalogSnapshot(
                harness="agy",
                status="unavailable",
                source="observed",
                detail=str(exc)[-1000:],
                refreshed_at=datetime.now(UTC),
            )

    def _manifest_cache_path(self) -> Path:
        return self.state_dir / "model-intelligence" / "manifest-v1.json"


def parse_codex_catalog(payload: bytes, *, bundled: bool = False) -> list[CatalogModel]:
    decoded = json.loads(payload.decode("utf-8"))
    raw_models = decoded.get("models") if isinstance(decoded, dict) else decoded
    if not isinstance(raw_models, list):
        raise ValueError("Codex catalog must contain a models array")
    models: list[CatalogModel] = []
    seen: set[str] = set()
    for raw_model in raw_models:
        if not isinstance(raw_model, dict):
            continue
        visibility = raw_model.get("visibility")
        if visibility not in {None, "list", "visible"}:
            continue
        slug = raw_model.get("slug") or raw_model.get("id") or raw_model.get("model")
        if not isinstance(slug, str):
            continue
        try:
            slug = _safe_text(slug)
        except ValueError:
            continue
        if slug.casefold() in seen:
            continue
        seen.add(slug.casefold())
        efforts: list[CodexReasoningEffort] = []
        raw_efforts = raw_model.get("supported_reasoning_levels", [])
        if isinstance(raw_efforts, list):
            for raw_effort in raw_efforts:
                value = raw_effort.get("effort") if isinstance(raw_effort, dict) else raw_effort
                if not isinstance(value, str):
                    continue
                try:
                    effort = CodexReasoningEffort(value)
                except ValueError:
                    continue
                if effort not in efforts:
                    efforts.append(effort)
        raw_default_effort = raw_model.get("default_reasoning_level")
        try:
            default_effort = (
                CodexReasoningEffort(raw_default_effort)
                if isinstance(raw_default_effort, str)
                else None
            )
        except ValueError:
            default_effort = None
        display_name = raw_model.get("display_name")
        if isinstance(display_name, str):
            try:
                display_name = _safe_text(display_name)
            except ValueError:
                display_name = slug
        models.append(
            CatalogModel(
                model=slug,
                display_name=display_name if isinstance(display_name, str) else slug,
                harness="codex",
                supported_efforts=efforts,
                default_effort=default_effort,
                source="codex_bundled" if bundled else "codex_cli",
                position=len(models),
            )
        )
    return models


def parse_agy_catalog(payload: bytes) -> list[CatalogModel]:
    text = _ANSI_ESCAPE.sub("", payload.decode("utf-8"))
    candidates: list[str] = []
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        decoded = None
    if isinstance(decoded, dict):
        raw_models = decoded.get("models")
        if isinstance(raw_models, list):
            for item in raw_models:
                if isinstance(item, str):
                    candidates.append(item)
                elif isinstance(item, dict):
                    name = item.get("name") or item.get("display_name") or item.get("model")
                    if isinstance(name, str):
                        candidates.append(name)
    elif isinstance(decoded, list):
        candidates.extend(item for item in decoded if isinstance(item, str))
    if not candidates:
        for raw_line in text.splitlines():
            line = raw_line.strip().strip("│|•*- ")
            if not line:
                continue
            cells = [cell.strip() for cell in re.split(r"\s*[│|]\s*", line) if cell.strip()]
            for cell in cells:
                normalized = re.sub(r"\s+", " ", cell)
                if normalized.casefold().startswith(_MODEL_PREFIXES):
                    candidates.append(normalized)
                    break
    models: list[CatalogModel] = []
    seen: set[str] = set()
    for candidate in candidates:
        try:
            model = _safe_text(candidate)
        except ValueError:
            continue
        if model.casefold() in seen:
            continue
        seen.add(model.casefold())
        models.append(
            CatalogModel(
                model=model,
                display_name=model,
                harness="agy",
                variant_effort=_agy_variant_effort(model),
                source="agy_cli",
                position=len(models),
            )
        )
    return models


def build_guide(workspace: Path, agy_model: str) -> dict[str, object]:
    resolved = workspace.expanduser().resolve()
    model = _safe_text(agy_model)
    posix_workspace = shlex.quote(str(resolved))
    posix_model = shlex.quote(model)
    powershell_workspace = _powershell_quote(str(resolved))
    powershell_model = _powershell_quote(model)
    return {
        "workspace": str(resolved),
        "agy_model": model,
        "shells": {
            "posix": _guide_sections(
                cd=f"cd -- {posix_workspace}",
                agy=f"agy --model {posix_model}",
            ),
            "powershell": _guide_sections(
                cd=f"Set-Location -LiteralPath {powershell_workspace}",
                agy=f"agy --model {powershell_model}",
            ),
        },
        "agy_tui": [
            {"id": "model", "command": "/model"},
            {"id": "mcp", "command": "/mcp"},
            {"id": "skills", "command": "/skills"},
            {"id": "agentbraid", "command": "/agentbraid"},
        ],
    }


def _guide_sections(*, cd: str, agy: str) -> list[dict[str, object]]:
    return [
        {
            "id": "first_setup",
            "commands": [
                {"id": "cd", "command": cd},
                {"id": "doctor", "command": "agentbraid doctor ."},
                {"id": "init", "command": "agentbraid init ."},
            ],
        },
        {
            "id": "daily_start",
            "commands": [
                {"id": "cd", "command": cd},
                {"id": "agy", "command": agy},
                {"id": "dashboard", "command": "agentbraid dashboard ."},
            ],
        },
        {"id": "model_setup", "commands": [{"id": "agy", "command": agy}]},
    ]


def _rank_models(
    models: Sequence[CatalogModel],
    *,
    harness: str,
    target_effort: CodexReasoningEffort,
    manifest: ModelManifest,
    statistics: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]:
    percentiles = _entry_percentiles(manifest.entries)
    ranked: list[tuple[tuple[float, ...], dict[str, object]]] = []
    for model in models:
        recommended_effort = (
            _recommended_effort(model.supported_efforts, target_effort)
            if harness == "codex"
            else None
        )
        external, evidence_ids = _external_score(
            manifest.entries,
            percentiles,
            model=model.model,
            harness=harness,
            effort=recommended_effort,
        )
        local = _local_statistics(statistics, model.model, recommended_effort)
        local_score = None
        if local["samples"] > 0:
            local_score = (local["successes"] + 1.0) / (local["samples"] + 2.0)
        retry_score = None
        if local["invocations"] > 0:
            retry_score = (local["invocations"] - local["retry_invocations"] + 1.0) / (
                local["invocations"] + 2.0
            )
        weights = (
            {"external": 0.4, "local": 0.55, "retry": 0.05}
            if local["samples"] >= 5
            else {"external": 0.7, "local": 0.25, "retry": 0.05}
        )
        components = {
            "external": external,
            "local": local_score,
            "retry": retry_score,
        }
        available_weight = sum(
            weights[key] for key, value in components.items() if value is not None
        )
        quality = (
            sum(weights[key] * value for key, value in components.items() if value is not None)
            / available_weight
            if available_weight
            else None
        )
        variant_distance = _variant_distance(model.variant_effort, target_effort)
        confidence = _confidence(local["samples"], len(evidence_ids))
        reason_codes = ["catalog_order"] if quality is None else ["quality_first"]
        if external is not None:
            reason_codes.append("external_evidence")
        if local_score is not None:
            reason_codes.append("local_success")
        if harness == "codex" and recommended_effort is not None:
            reason_codes.append("effort_fit")
        if harness == "agy" and model.variant_effort is not None:
            reason_codes.append("variant_fit")
        result: dict[str, object] = {
            "model": model.model,
            "display_name": model.display_name,
            "recommended_effort": (
                recommended_effort.value if recommended_effort is not None else None
            ),
            "variant_effort": (
                model.variant_effort.value if model.variant_effort is not None else None
            ),
            "confidence": confidence,
            "quality_score": round(quality, 4) if quality is not None else None,
            "external_score": round(external, 4) if external is not None else None,
            "local_success": round(local_score, 4) if local_score is not None else None,
            "sample_count": int(local["samples"]),
            "reason_codes": reason_codes,
            "evidence_ids": evidence_ids,
            "efficiency": {
                "average_tokens": local["average_tokens"],
                "average_duration_seconds": local["average_duration_seconds"],
                "retry_invocations": int(local["retry_invocations"]),
            },
        }
        if harness == "agy":
            result["launch_commands"] = {
                "posix": f"agy --model {shlex.quote(model.model)}",
                "powershell": f"agy --model {_powershell_quote(model.model)}",
            }
        quality_sort = quality if quality is not None else -1.0
        token_sort = _negative_optional(local["average_tokens"])
        duration_sort = _negative_optional(local["average_duration_seconds"])
        no_score_variant = -float(variant_distance) if quality is None else 0.0
        sort_key = (
            1.0 if quality is not None else 0.0,
            quality_sort,
            no_score_variant,
            token_sort,
            duration_sort,
            -float(model.position),
        )
        ranked.append((sort_key, result))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [result for _, result in ranked]


def _entry_percentiles(entries: Sequence[BenchmarkEntry]) -> dict[str, float]:
    eligible = [entry for entry in entries if _entry_is_eligible(entry)]
    groups: dict[tuple[str, str, str, str], list[BenchmarkEntry]] = defaultdict(list)
    for entry in eligible:
        groups[(entry.source_id, entry.benchmark, entry.version, entry.harness)].append(entry)
    percentiles: dict[str, float] = {}
    for group in groups.values():
        ordered = sorted(
            group,
            key=lambda item: item.raw_score,
            reverse=group[0].higher_is_better,
        )
        if len(ordered) == 1:
            percentiles[ordered[0].evidence_id] = 0.5
            continue
        for entry in ordered:
            positions = [
                index
                for index, candidate in enumerate(ordered)
                if candidate.raw_score == entry.raw_score
            ]
            average_position = sum(positions) / len(positions)
            percentiles[entry.evidence_id] = 1.0 - average_position / (len(ordered) - 1)
    return percentiles


def _external_score(
    entries: Sequence[BenchmarkEntry],
    percentiles: Mapping[str, float],
    *,
    model: str,
    harness: str,
    effort: CodexReasoningEffort | None,
) -> tuple[float | None, list[str]]:
    weighted: list[tuple[float, float]] = []
    evidence_ids: list[str] = []
    for entry in entries:
        if not _entry_is_eligible(entry):
            continue
        if entry.harness != harness or entry.model.casefold() != model.casefold():
            continue
        if entry.effort is not None and entry.effort != effort:
            continue
        percentile = percentiles.get(entry.evidence_id)
        if percentile is None:
            continue
        weight = _CATEGORY_WEIGHT[entry.category]
        weighted.append((percentile, weight))
        evidence_ids.append(entry.evidence_id)
    if not weighted:
        return None, []
    return sum(value * weight for value, weight in weighted) / sum(
        weight for _, weight in weighted
    ), evidence_ids


def _entry_is_eligible(entry: BenchmarkEntry) -> bool:
    return (
        entry.license_status == "redistributable"
        and entry.model_match_confidence == "exact"
        and entry.source_date >= date.today() - timedelta(days=730)
    )


def _local_statistics(
    statistics: Sequence[Mapping[str, object]],
    model: str,
    effort: CodexReasoningEffort | None,
) -> dict[str, float]:
    exact = [
        item
        for item in statistics
        if str(item.get("model", "")).casefold() == model.casefold()
        and item.get("reasoning_effort") == (effort.value if effort is not None else None)
        and item.get("scope") == "effort"
    ]
    candidates = exact or [
        item
        for item in statistics
        if str(item.get("model", "")).casefold() == model.casefold()
        and item.get("scope") == "model"
    ]
    if not candidates:
        return {
            "successes": 0.0,
            "samples": 0.0,
            "invocations": 0.0,
            "retry_invocations": 0.0,
            "average_tokens": 0.0,
            "average_duration_seconds": 0.0,
        }
    item = candidates[0]
    return {
        key: _numeric_stat(item.get(key))
        for key in (
            "successes",
            "samples",
            "invocations",
            "retry_invocations",
            "average_tokens",
            "average_duration_seconds",
        )
    }


def _recommended_effort(
    supported: Sequence[CodexReasoningEffort],
    target: CodexReasoningEffort,
) -> CodexReasoningEffort | None:
    allowed = [
        effort
        for effort in supported
        if effort not in {CodexReasoningEffort.MAX, CodexReasoningEffort.ULTRA}
        and _EFFORT_ORDER[effort] <= _EFFORT_ORDER[target]
    ]
    return max(allowed, key=_EFFORT_ORDER.__getitem__) if allowed else None


def _agy_variant_effort(model: str) -> CodexReasoningEffort | None:
    match = re.search(r"(?:^|[ (])(low|medium|high|thinking)\)?$", model, re.IGNORECASE)
    if match is None:
        return None
    suffix = match.group(1).casefold()
    if suffix == "thinking":
        return CodexReasoningEffort.XHIGH
    try:
        return CodexReasoningEffort(suffix)
    except ValueError:
        return None


def _variant_distance(
    variant: CodexReasoningEffort | None,
    target: CodexReasoningEffort,
) -> int:
    if variant is None:
        return 10
    variant_rank = _EFFORT_ORDER[variant]
    target_rank = _EFFORT_ORDER[target]
    return abs(target_rank - variant_rank) + (10 if variant_rank > target_rank else 0)


def _confidence(samples: float, evidence_count: int) -> Literal["low", "medium", "high"]:
    if samples >= 5 and evidence_count >= 2:
        return "high"
    if samples >= 5 or evidence_count:
        return "medium"
    return "low"


def _observed_snapshot(harness: str, models: Sequence[str]) -> CatalogSnapshot:
    typed_harness: Literal["codex", "agy"] = "codex" if harness == "codex" else "agy"
    return CatalogSnapshot(
        harness=typed_harness,
        models=[
            CatalogModel(
                model=model,
                display_name=model,
                harness=typed_harness,
                variant_effort=_agy_variant_effort(model) if typed_harness == "agy" else None,
                source="observed",
                position=index,
            )
            for index, model in enumerate(_deduplicate_models(models))
        ],
        status="not_refreshed" if not models else "fallback",
        source="observed",
    )


def _with_observed_models(snapshot: CatalogSnapshot, observed: Sequence[str]) -> CatalogSnapshot:
    models = list(snapshot.models)
    seen = {item.model.casefold() for item in models}
    for candidate in _deduplicate_models(observed):
        if candidate.casefold() in seen:
            continue
        seen.add(candidate.casefold())
        models.append(
            CatalogModel(
                model=candidate,
                display_name=candidate,
                harness=snapshot.harness,
                variant_effort=(
                    _agy_variant_effort(candidate) if snapshot.harness == "agy" else None
                ),
                source="observed",
                position=len(models),
            )
        )
    status = snapshot.status
    if status == "unavailable" and models:
        status = "fallback"
    return snapshot.model_copy(update={"models": models, "status": status})


def _deduplicate_models(models: Sequence[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for candidate in models:
        try:
            model = _safe_text(candidate)
        except ValueError:
            continue
        if model.casefold() not in seen:
            seen.add(model.casefold())
            result.append(model)
    return result


def _validate_manifest(payload: bytes) -> ModelManifest:
    if len(payload) > CATALOG_OUTPUT_LIMIT:
        raise StateError("external model manifest exceeded the download limit")
    try:
        return ModelManifest.model_validate_json(payload)
    except (ValidationError, ValueError) as exc:
        raise StateError("model intelligence manifest is invalid", detail=str(exc)) from exc


def _manifest_details(
    manifest: ModelManifest,
    origin: Literal["cache", "packaged"],
) -> dict[str, object]:
    stale_before = date.today() - timedelta(days=730)
    return {
        "schema_version": manifest.schema_version,
        "manifest_version": manifest.manifest_version,
        "generated_at": manifest.generated_at.isoformat(),
        "origin": origin,
        "update_url": EXTERNAL_MANIFEST_URL,
        "sources": [source.model_dump(mode="json") for source in manifest.sources],
        "entries": [
            {
                **entry.model_dump(mode="json"),
                "eligible": _entry_is_eligible(entry),
                "stale": entry.source_date < stale_before,
            }
            for entry in manifest.entries
        ],
    }


def _safe_text(value: str) -> str:
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("control characters are not allowed")
    normalized = re.sub(r"\s+", " ", value.strip())
    if not normalized:
        raise ValueError("value must not be empty")
    if len(normalized) > 200:
        raise ValueError("value is too long")
    return normalized


def _powershell_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _validate_https_url(value: str, allowed_hosts: frozenset[str]) -> None:
    parsed = urlparse(value)
    if parsed.scheme != "https" or parsed.hostname not in allowed_hosts:
        raise ValueError("URL is outside the HTTPS allowlist")
    if parsed.username or parsed.password:
        raise ValueError("URL credentials are not allowed")


def _command_error(result: CommandResult) -> str:
    detail = result.stderr.decode("utf-8", errors="replace").strip()
    return detail[-1000:] or f"command exited with {result.returncode}"


def _read_bounded_stream(
    stream: IO[bytes],
    limit: int,
    process: subprocess.Popen[bytes],
) -> bytes:
    output = bytearray()
    while True:
        chunk = stream.read(min(64 * 1024, limit + 1 - len(output)))
        if not chunk:
            return bytes(output)
        output.extend(chunk)
        if len(output) > limit:
            process.kill()
            raise _CatalogFailure(f"command output exceeded {limit} bytes")


def _assert_bounded_command_result(result: CommandResult) -> None:
    if len(result.stdout) + len(result.stderr) > CATALOG_OUTPUT_LIMIT:
        raise _CatalogFailure(f"command output exceeded {CATALOG_OUTPUT_LIMIT} bytes")


def _negative_optional(value: float) -> float:
    return -value if value > 0 else -float("inf")


def _numeric_stat(value: object) -> float:
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else 0.0
