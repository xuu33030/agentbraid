from __future__ import annotations

import json
import shlex
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest

from agentbraid.errors import StateError
from agentbraid.model_intelligence import (
    CATALOG_OUTPUT_LIMIT,
    CATALOG_TIMEOUT_SECONDS,
    EXTERNAL_MANIFEST_URL,
    CatalogRunner,
    CommandResult,
    ModelIntelligence,
    SubprocessCatalogRunner,
    _CatalogFailure,
    build_guide,
    parse_agy_catalog,
    parse_codex_catalog,
)
from agentbraid.models import Executor, WorkloadComplexity


class FakeRunner(CatalogRunner):
    def __init__(
        self,
        responses: dict[tuple[str, ...], list[CommandResult | BaseException]],
    ) -> None:
        self.responses = responses
        self.calls: list[tuple[tuple[str, ...], Path, int, int]] = []

    def run(
        self,
        argv: list[str] | tuple[str, ...],
        *,
        cwd: Path,
        timeout: int,
        max_output_bytes: int,
    ) -> CommandResult:
        key = tuple(argv)
        self.calls.append((key, cwd, timeout, max_output_bytes))
        response = self.responses[key].pop(0)
        if isinstance(response, BaseException):
            raise response
        return response


class FakeDownloader:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.calls: list[tuple[str, int, int]] = []

    def download(self, url: str, *, timeout: int, max_bytes: int) -> bytes:
        self.calls.append((url, timeout, max_bytes))
        return self.payload


def codex_payload(*models: dict[str, Any]) -> bytes:
    return json.dumps({"models": list(models)}).encode()


def codex_model(
    slug: str,
    *efforts: str,
    display_name: str | None = None,
) -> dict[str, Any]:
    return {
        "slug": slug,
        "display_name": display_name or slug,
        "visibility": "list",
        "default_reasoning_level": efforts[0] if efforts else None,
        "supported_reasoning_levels": [{"effort": effort} for effort in efforts],
    }


def manifest_payload(entries: list[dict[str, Any]] | None = None) -> bytes:
    return json.dumps(
        {
            "schema_version": "1",
            "manifest_version": "test-v1",
            "generated_at": date.today().isoformat(),
            "sources": [
                {
                    "source_id": "open-benchmark",
                    "name": "Open benchmark",
                    "url": "https://github.com/SWE-bench/swe-bench",
                    "license_status": "redistributable",
                    "notes": "test source",
                },
                {
                    "source_id": "reference-site",
                    "name": "Reference site",
                    "url": "https://artificialanalysis.ai/data-api",
                    "license_status": "reference_only",
                    "notes": "reference only",
                },
            ],
            "entries": entries or [],
        }
    ).encode()


def benchmark_entry(
    evidence_id: str,
    model: str,
    score: float,
    *,
    effort: str | None = None,
    harness: str = "codex",
    license_status: str = "redistributable",
    source_id: str = "open-benchmark",
    source_date: date | None = None,
) -> dict[str, Any]:
    return {
        "evidence_id": evidence_id,
        "source_id": source_id,
        "model": model,
        "effort": effort,
        "harness": harness,
        "benchmark": "SWE-bench Verified",
        "category": "agentic_coding",
        "raw_score": score,
        "scale_min": 0,
        "scale_max": 100,
        "higher_is_better": True,
        "source_date": (source_date or date.today()).isoformat(),
        "version": "1",
        "source_url": "https://github.com/SWE-bench/swe-bench",
        "license_status": license_status,
        "model_match_confidence": "exact",
    }


def catalog_runner(
    codex: bytes,
    agy: bytes = b"Gemini Flash Medium\nGemini Flash Low\nGemini Flash High\n",
) -> FakeRunner:
    return FakeRunner(
        {
            ("codex", "debug", "models"): [CommandResult(0, codex)],
            ("agy", "models"): [CommandResult(0, agy)],
        }
    )


def test_subprocess_catalog_runner_enforces_timeout_and_stream_limit(tmp_path: Path) -> None:
    runner = SubprocessCatalogRunner()
    success = runner.run(
        [sys.executable, "-I", "-S", "-c", "print('catalog-ok')"],
        cwd=tmp_path,
        timeout=2,
        max_output_bytes=100,
    )

    assert success.returncode == 0
    assert success.stdout == b"catalog-ok\n"
    with pytest.raises(_CatalogFailure, match="output exceeded"):
        runner.run(
            [sys.executable, "-I", "-S", "-c", "print('x' * 1000)"],
            cwd=tmp_path,
            timeout=2,
            max_output_bytes=100,
        )
    with pytest.raises(_CatalogFailure, match="timed out"):
        runner.run(
            [sys.executable, "-I", "-S", "-c", "import time; time.sleep(2)"],
            cwd=tmp_path,
            timeout=1,
            max_output_bytes=100,
        )


def test_catalog_refresh_parses_deduplicates_and_uses_process_cache(tmp_path: Path) -> None:
    runner = catalog_runner(
        codex_payload(
            codex_model("gpt-a", "low", "medium", "high"),
            codex_model("gpt-a", "low"),
            codex_model("bad\nmodel", "low"),
        ),
        b"Gemini Flash Medium\nGemini Flash Medium\nClaude Sonnet Thinking\n",
    )
    intelligence = ModelIntelligence(tmp_path / "state", runner=runner)

    first = intelligence.refresh_catalogs(
        tmp_path,
        codex_binary="codex",
        observed_codex=[],
        observed_host=[],
    )
    second = intelligence.refresh_catalogs(
        tmp_path,
        codex_binary="codex",
        observed_codex=[],
        observed_host=[],
    )

    assert first["codex"] == ["gpt-a"]
    assert first["host"] == ["Gemini Flash Medium", "Claude Sonnet Thinking"]
    assert second == first
    assert [call[0] for call in runner.calls] == [
        ("codex", "debug", "models"),
        ("agy", "models"),
    ]
    assert all(call[2:] == (CATALOG_TIMEOUT_SECONDS, CATALOG_OUTPUT_LIMIT) for call in runner.calls)


def test_codex_catalog_falls_back_to_bundled_and_observed_agy(tmp_path: Path) -> None:
    runner = FakeRunner(
        {
            ("codex", "debug", "models"): [CommandResult(1, b"", b"offline")],
            ("codex", "debug", "models", "--bundled"): [
                CommandResult(0, codex_payload(codex_model("gpt-bundled", "medium")))
            ],
            ("agy", "models"): [FileNotFoundError("agy missing")],
        }
    )
    intelligence = ModelIntelligence(tmp_path / "state", runner=runner)

    options = intelligence.refresh_catalogs(
        tmp_path,
        codex_binary="codex",
        observed_codex=["observed-codex"],
        observed_host=["observed-agy"],
    )

    assert options["codex"] == ["gpt-bundled", "observed-codex"]
    assert options["host"] == ["observed-agy"]
    assert options["catalogs"]["codex"]["source"] == "codex_bundled"
    assert options["catalogs"]["agy"]["status"] == "fallback"


@pytest.mark.parametrize(
    "failure",
    [
        subprocess.TimeoutExpired(["codex"], 10),
        FileNotFoundError("codex missing"),
    ],
)
def test_catalog_timeout_and_missing_binary_remain_manual_fallback(
    tmp_path: Path,
    failure: BaseException,
) -> None:
    runner = FakeRunner(
        {
            ("codex", "debug", "models"): [failure],
            ("codex", "debug", "models", "--bundled"): [failure],
            ("agy", "models"): [CommandResult(0, b"Gemini Flash Medium\n")],
        }
    )

    options = ModelIntelligence(tmp_path / "state", runner=runner).refresh_catalogs(
        tmp_path,
        codex_binary="codex",
        observed_codex=["manual-codex"],
        observed_host=[],
    )

    assert options["codex"] == ["manual-codex"]
    assert options["catalogs"]["codex"]["status"] == "fallback"


def test_catalog_parsers_reject_invalid_json_controls_and_duplicates() -> None:
    with pytest.raises(json.JSONDecodeError):
        parse_codex_catalog(b"not-json")

    agy = parse_agy_catalog(
        b"Gemini Flash Medium\nGemini Flash Medium\nGemini Bad\x00Model\nGPT-OSS High\n"
    )

    assert [model.model for model in agy] == ["Gemini Flash Medium", "GPT-OSS High"]


def test_external_manifest_update_is_fixed_validated_atomic_and_retains_cache(
    tmp_path: Path,
) -> None:
    downloader = FakeDownloader(manifest_payload())
    intelligence = ModelIntelligence(tmp_path / "state", downloader=downloader)

    updated = intelligence.refresh_external_manifest()
    cache_path = tmp_path / "state" / "model-intelligence" / "manifest-v1.json"
    original = cache_path.read_bytes()
    downloader.payload = b'{"schema_version":"1","sources":[]}'

    with pytest.raises(StateError, match="manifest is invalid"):
        intelligence.refresh_external_manifest()

    assert updated.manifest_version == "test-v1"
    assert cache_path.read_bytes() == original
    assert downloader.calls[0] == (
        EXTERNAL_MANIFEST_URL,
        CATALOG_TIMEOUT_SECONDS,
        CATALOG_OUTPUT_LIMIT,
    )


def test_external_manifest_rejects_size_allowlist_future_and_invalid_ranges(
    tmp_path: Path,
) -> None:
    oversized = FakeDownloader(b"x" * (CATALOG_OUTPUT_LIMIT + 1))
    with pytest.raises(StateError, match="download limit"):
        ModelIntelligence(tmp_path / "oversized", downloader=oversized).refresh_external_manifest()

    entry = benchmark_entry("bad-range", "gpt-a", 50)
    entry["scale_max"] = 10
    payload = json.loads(manifest_payload([entry]))
    payload["sources"][0]["url"] = "https://example.com/not-allowed"
    invalid = FakeDownloader(json.dumps(payload).encode())
    with pytest.raises(StateError, match="manifest is invalid"):
        ModelIntelligence(tmp_path / "invalid", downloader=invalid).refresh_external_manifest()

    stale_payload = json.loads(manifest_payload())
    stale_payload["generated_at"] = (date.today() - timedelta(days=731)).isoformat()
    stale = FakeDownloader(json.dumps(stale_payload).encode())
    with pytest.raises(StateError, match="manifest is stale"):
        ModelIntelligence(tmp_path / "stale", downloader=stale).refresh_external_manifest()

    older_payload = json.loads(manifest_payload())
    older_payload["generated_at"] = (date.today() - timedelta(days=1)).isoformat()
    older = FakeDownloader(manifest_payload())
    older_intelligence = ModelIntelligence(tmp_path / "older", downloader=older)
    older_intelligence.refresh_external_manifest()
    older.payload = json.dumps(older_payload).encode()
    with pytest.raises(StateError, match="older than the local manifest"):
        older_intelligence.refresh_external_manifest()


def test_recommendation_weights_change_at_five_samples_and_ignore_reference_data(
    tmp_path: Path,
) -> None:
    runner = catalog_runner(
        codex_payload(
            codex_model("gpt-external", "low", "medium", "high"),
            codex_model("gpt-local", "low", "medium", "high"),
        )
    )
    downloader = FakeDownloader(
        manifest_payload(
            [
                benchmark_entry("external-best", "gpt-external", 100, effort="medium"),
                benchmark_entry("local-low", "gpt-local", 0, effort="medium"),
                benchmark_entry(
                    "restricted",
                    "gpt-local",
                    100,
                    effort="medium",
                    license_status="reference_only",
                    source_id="reference-site",
                ),
                benchmark_entry(
                    "stale",
                    "gpt-local",
                    100,
                    effort="medium",
                    source_date=date.today() - timedelta(days=731),
                ),
            ]
        )
    )
    intelligence = ModelIntelligence(tmp_path / "state", runner=runner, downloader=downloader)
    intelligence.refresh_catalogs(
        tmp_path,
        codex_binary="codex",
        observed_codex=[],
        observed_host=[],
    )
    intelligence.refresh_external_manifest()
    small_sample = {
        Executor.CODEX: [
            model_stat("gpt-external", successes=0, samples=4, retries=4, tokens=999_999),
            model_stat("gpt-local", successes=4, samples=4, retries=0, tokens=1),
        ],
        Executor.HOST: [],
    }
    mature_sample = {
        Executor.CODEX: [
            model_stat("gpt-external", successes=0, samples=5, retries=5, tokens=999_999),
            model_stat("gpt-local", successes=5, samples=5, retries=0, tokens=1),
        ],
        Executor.HOST: [],
    }

    external_first = intelligence.recommend(
        WorkloadComplexity.STANDARD,
        small_sample,
        [],
        [],
    )
    local_first = intelligence.recommend(
        WorkloadComplexity.STANDARD,
        mature_sample,
        [],
        [],
    )

    assert external_first["codex"][0]["model"] == "gpt-external"
    assert local_first["codex"][0]["model"] == "gpt-local"
    assert "restricted" not in local_first["codex"][0]["evidence_ids"]
    assert "stale" not in local_first["codex"][0]["evidence_ids"]


def model_stat(
    model: str,
    *,
    successes: int,
    samples: int,
    retries: int,
    tokens: int,
) -> dict[str, object]:
    return {
        "model": model,
        "reasoning_effort": None,
        "scope": "model",
        "successes": successes,
        "samples": samples,
        "invocations": samples,
        "retry_invocations": retries,
        "average_tokens": tokens,
        "average_duration_seconds": 1,
    }


def test_effort_downgrades_without_auto_recommending_max_or_ultra(tmp_path: Path) -> None:
    runner = catalog_runner(
        codex_payload(codex_model("gpt-limited", "medium", "xhigh", "max", "ultra"))
    )
    intelligence = ModelIntelligence(tmp_path / "state", runner=runner)
    intelligence.refresh_catalogs(
        tmp_path,
        codex_binary="codex",
        observed_codex=[],
        observed_host=[],
    )

    complex_result = intelligence.recommend(
        WorkloadComplexity.COMPLEX,
        {},
        [],
        [],
    )
    high_risk_result = intelligence.recommend(
        WorkloadComplexity.HIGH_RISK,
        {},
        [],
        [],
    )

    assert complex_result["codex"][0]["recommended_effort"] == "medium"
    assert high_risk_result["codex"][0]["recommended_effort"] == "xhigh"


def test_agy_variants_follow_complexity_without_fabricated_scores(tmp_path: Path) -> None:
    runner = catalog_runner(
        codex_payload(codex_model("gpt-a", "low")),
        (
            b"Gemini Flash (Medium)\nGemini Flash (High)\nGemini Flash (Low)\n"
            b"Claude Sonnet (Thinking)\n"
        ),
    )
    intelligence = ModelIntelligence(tmp_path / "state", runner=runner)
    intelligence.refresh_catalogs(
        tmp_path,
        codex_binary="codex",
        observed_codex=[],
        observed_host=[],
    )

    quick = intelligence.recommend(WorkloadComplexity.QUICK, {}, [], [])
    high_risk = intelligence.recommend(WorkloadComplexity.HIGH_RISK, {}, [], [])

    assert quick["agy"][0]["model"] == "Gemini Flash (Low)"
    assert high_risk["agy"][0]["model"] == "Claude Sonnet (Thinking)"
    assert quick["agy"][0]["quality_score"] is None


def test_guide_quotes_posix_and_powershell_injection_characters(tmp_path: Path) -> None:
    workspace = tmp_path / "repo; touch escaped ' path"
    model = "Gemini'; Write-Output hacked; 'High"

    guide = build_guide(workspace, model)
    posix_sections = guide["shells"]["posix"]
    powershell_sections = guide["shells"]["powershell"]
    posix_cd = posix_sections[0]["commands"][0]["command"]
    posix_agy = posix_sections[1]["commands"][1]["command"]
    powershell_cd = powershell_sections[0]["commands"][0]["command"]
    powershell_agy = powershell_sections[1]["commands"][1]["command"]

    assert shlex.split(posix_cd) == ["cd", "--", str(workspace.resolve())]
    assert shlex.split(posix_agy) == ["agy", "--model", model]
    assert powershell_cd.startswith("Set-Location -LiteralPath '")
    assert "touch escaped '' path'" in powershell_cd
    assert powershell_agy == "agy --model 'Gemini''; Write-Output hacked; ''High'"

    with pytest.raises(ValueError, match="control characters"):
        build_guide(tmp_path, "bad\nmodel")
