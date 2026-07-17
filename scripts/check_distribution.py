from __future__ import annotations

import argparse
import subprocess
import sys
import tarfile
import tempfile
import venv
import zipfile
from email.parser import Parser
from pathlib import Path

PACKAGE_VERSION = "0.2.0a2"


def has_suffix(names: list[str], suffix: str) -> bool:
    return any(name.endswith(suffix) for name in names)


def distribution_files(directory: Path) -> tuple[Path, Path]:
    wheels = sorted(directory.glob("agentbraid-*.whl"))
    source_archives = sorted(directory.glob("agentbraid-*.tar.gz"))
    if len(wheels) != 1 or len(source_archives) != 1:
        raise SystemExit(
            f"expected one wheel and one sdist in {directory}; "
            f"found wheels={len(wheels)}, sdists={len(source_archives)}"
        )
    return wheels[0], source_archives[0]


def check_wheel(path: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        metadata_name = next(
            (name for name in names if name.endswith(".dist-info/METADATA")),
            None,
        )
        if metadata_name is None:
            raise SystemExit("wheel has no dist-info METADATA")
        metadata = Parser().parsestr(archive.read(metadata_name).decode())
    required_suffixes = (
        "agentbraid/py.typed",
        "agentbraid/dashboard_assets/index.html",
        "agentbraid/dashboard_assets/app.css",
        "agentbraid/dashboard_assets/app.js",
        "agentbraid/dashboard_assets/locales.json",
        ".dist-info/licenses/LICENSE",
        ".dist-info/licenses/NOTICE",
        ".dist-info/licenses/THIRD_PARTY_NOTICES.md",
    )
    missing = [suffix for suffix in required_suffixes if not has_suffix(names, suffix)]
    if missing:
        raise SystemExit(f"wheel is missing required files: {', '.join(missing)}")
    forbidden = [name for name in names if "/tests/" in name or name.endswith((".db", ".env"))]
    if forbidden:
        raise SystemExit(f"wheel contains forbidden files: {', '.join(forbidden)}")
    if metadata.get("Name") != "agentbraid":
        raise SystemExit("wheel metadata has an unexpected project name")
    if metadata.get("Version") != PACKAGE_VERSION:
        raise SystemExit("wheel metadata has an unexpected version")
    if metadata.get("License-Expression") != "Apache-2.0":
        raise SystemExit("wheel metadata has an unexpected license expression")


def check_sdist(path: Path) -> None:
    with tarfile.open(path, "r:gz") as archive:
        names = archive.getnames()
    required = {"LICENSE", "NOTICE", "THIRD_PARTY_NOTICES.md", "pyproject.toml"}
    basenames = {Path(name).name for name in names}
    missing = sorted(required - basenames)
    if missing:
        raise SystemExit(f"sdist is missing required files: {', '.join(missing)}")


def check_metadata(wheel: Path, source_archive: Path) -> None:
    subprocess.run(
        [
            sys.executable,
            "-m",
            "twine",
            "check",
            str(wheel),
            str(source_archive),
        ],
        check=True,
    )


def smoke_install(wheel: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="agentbraid-wheel-") as temporary_dir:
        environment = Path(temporary_dir) / "venv"
        venv.EnvBuilder(with_pip=True, clear=True).create(environment)
        python = (
            environment / "Scripts" / "python.exe"
            if sys.platform == "win32"
            else environment / "bin" / "python"
        )
        subprocess.run(
            [str(python), "-m", "pip", "install", "--no-deps", str(wheel)],
            check=True,
        )
        completed = subprocess.run(
            [str(python), "-m", "agentbraid", "--version"],
            capture_output=True,
            check=True,
            text=True,
        )
        if completed.stdout.strip() != f"agentbraid {PACKAGE_VERSION}":
            raise SystemExit(f"unexpected wheel CLI output: {completed.stdout.strip()}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", nargs="?", type=Path, default=Path("dist"))
    args = parser.parse_args()
    wheel, source_archive = distribution_files(args.directory)
    check_metadata(wheel, source_archive)
    check_wheel(wheel)
    check_sdist(source_archive)
    smoke_install(wheel)
    print(f"validated {wheel.name} and {source_archive.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
