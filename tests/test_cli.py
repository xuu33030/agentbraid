from __future__ import annotations

import json
from pathlib import Path

from agentbraid import __version__
from agentbraid.cli import main


def test_version_is_alpha() -> None:
    assert __version__ == "0.2.0a2"


def test_doctor_json_reports_checks(capsys: object, tmp_path: Path) -> None:
    exit_code = main(["doctor", str(tmp_path), "--json"])
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    payload = json.loads(captured.out)

    assert exit_code == 1
    assert [item["name"] for item in payload] == ["python", "git", "codex", "workspace"]
    assert payload[-1]["ok"] is False


def test_dashboard_command_forwards_local_options(
    monkeypatch: object,
    tmp_path: Path,
) -> None:
    received: dict[str, object] = {}

    def fake_dashboard(path: Path, *, port: int, open_browser: bool) -> None:
        received.update(path=path, port=port, open_browser=open_browser)

    monkeypatch.setattr("agentbraid.dashboard.run_dashboard", fake_dashboard)  # type: ignore[attr-defined]

    exit_code = main(["dashboard", str(tmp_path), "--port", "8123", "--no-open"])

    assert exit_code == 0
    assert received == {"path": tmp_path, "port": 8123, "open_browser": False}


def test_dashboard_command_reports_configuration_error(capsys: object, tmp_path: Path) -> None:
    exit_code = main(["dashboard", str(tmp_path), "--port", "70000", "--no-open"])
    captured = capsys.readouterr()  # type: ignore[attr-defined]

    assert exit_code == 1
    assert "Dashboard port" in captured.err
