from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from agentbraid.models import HostTaskResult, RunSnapshot, RunStatus, StartRunRequest, TaskStatus
from agentbraid.redaction import redact_text

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "examples"
MARKDOWN_LINK = re.compile(r"\[[^\]]+\]\(([^)]+)\)")


def load_example(name: str) -> dict[str, Any]:
    payload: object = json.loads((EXAMPLES / name).read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def test_start_run_example_matches_contract() -> None:
    payload = load_example("start-run.json")

    request = StartRunRequest.model_validate(payload["request"])

    assert request.delivery_mode.value == "integration_branch"
    assert request.host_model == "antigravity-auto"


def test_host_result_example_matches_contract() -> None:
    payload = load_example("host-result.json")

    result = HostTaskResult.model_validate(payload["result"])

    assert payload["host_id"] == "antigravity-host"
    assert result.commit_sha == "1" * 40
    assert all(evidence.passed for evidence in result.validations)


def test_redacted_run_example_matches_contract() -> None:
    snapshot = RunSnapshot.model_validate(load_example("redacted-run.json"))

    assert snapshot.status == RunStatus.COMPLETED
    assert snapshot.integration_branch == "agentbraid/integration/run-demo-red"
    assert snapshot.tasks
    assert all(task.status == TaskStatus.SUCCEEDED for task in snapshot.tasks)


def test_examples_need_no_additional_redaction() -> None:
    for path in sorted(EXAMPLES.glob("*.json")):
        content = path.read_text(encoding="utf-8")
        assert redact_text(content) == content, path


def test_local_markdown_links_resolve() -> None:
    markdown_files = [*ROOT.glob("*.md"), *(ROOT / "docs").rglob("*.md")]
    for markdown_file in sorted(markdown_files):
        content = markdown_file.read_text(encoding="utf-8")
        for target in MARKDOWN_LINK.findall(content):
            if target.startswith(("#", "https://", "http://", "mailto:")):
                continue
            local_target = target.split("#", maxsplit=1)[0]
            assert (markdown_file.parent / local_target).exists(), (
                f"broken link in {markdown_file.relative_to(ROOT)}: {target}"
            )
