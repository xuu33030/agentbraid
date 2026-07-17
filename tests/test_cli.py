from __future__ import annotations

import json
from pathlib import Path

from agentbraid import __version__
from agentbraid.cli import main


def test_version_is_alpha() -> None:
    assert __version__ == "0.1.0a2"


def test_doctor_json_reports_checks(capsys: object, tmp_path: Path) -> None:
    exit_code = main(["doctor", str(tmp_path), "--json"])
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    payload = json.loads(captured.out)

    assert exit_code == 1
    assert [item["name"] for item in payload] == ["python", "git", "codex", "workspace"]
    assert payload[-1]["ok"] is False
