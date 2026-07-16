from __future__ import annotations

import json
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, distribution
from typing import TypedDict

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name


class LicenseRecord(TypedDict):
    Name: str
    Version: str
    License: str


_ALLOWED_MARKERS = (
    "apache",
    "bsd",
    "cc0",
    "isc",
    "mit",
    "mozilla public license",
    "mpl",
    "psf",
    "python software foundation",
    "unlicense",
    "zope public license",
)
_FORBIDDEN_MARKERS = (
    "affero",
    "agpl",
    "business source",
    "busl",
    "gpl",
    "lgpl",
    "proprietary",
    "sspl",
    "unknown",
)


def parse_license_record(value: object) -> LicenseRecord:
    if not isinstance(value, dict):
        raise SystemExit("pip-licenses returned a non-object record")
    name = value.get("Name")
    version = value.get("Version")
    license_name = value.get("License")
    if not all(isinstance(item, str) for item in (name, version, license_name)):
        raise SystemExit("pip-licenses returned a record with invalid fields")
    assert isinstance(name, str)
    assert isinstance(version, str)
    assert isinstance(license_name, str)
    return {"Name": name, "Version": version, "License": license_name}


def runtime_package_names(root: str = "agentbraid") -> list[str]:
    pending = [(canonicalize_name(root), "")]
    processed: set[tuple[str, str]] = set()
    selected: set[str] = set()

    while pending:
        package_name, active_extra = pending.pop()
        scope = (package_name, active_extra)
        if scope in processed:
            continue
        processed.add(scope)
        try:
            package = distribution(package_name)
        except PackageNotFoundError as error:
            raise SystemExit(f"runtime dependency is not installed: {package_name}") from error
        installed_name = package.metadata["Name"]
        selected.add(canonicalize_name(installed_name))
        environment = {"extra": active_extra}
        for raw_requirement in package.requires or ():
            try:
                requirement = Requirement(raw_requirement)
            except InvalidRequirement as error:
                raise SystemExit(
                    f"invalid requirement metadata for {package_name}: {raw_requirement}"
                ) from error
            if requirement.marker is not None and not requirement.marker.evaluate(environment):
                continue
            dependency_name = canonicalize_name(requirement.name)
            pending.append((dependency_name, ""))
            pending.extend((dependency_name, extra) for extra in sorted(requirement.extras))

    return sorted(selected)


def installed_licenses() -> list[LicenseRecord]:
    package_names = runtime_package_names()
    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "piplicenses",
            "--format=json",
            "--packages",
            *package_names,
        ],
        capture_output=True,
        check=True,
        text=True,
    )
    payload: object = json.loads(completed.stdout)
    if not isinstance(payload, list):
        raise SystemExit("pip-licenses returned a non-list payload")
    records = [parse_license_record(record) for record in payload]
    reported = {canonicalize_name(record["Name"]) for record in records}
    missing = sorted(set(package_names) - reported)
    if missing:
        raise SystemExit(f"pip-licenses omitted runtime packages: {', '.join(missing)}")
    return records


def main() -> int:
    rejected: list[str] = []
    records = installed_licenses()
    for record in records:
        license_name = record["License"].casefold()
        forbidden = any(marker in license_name for marker in _FORBIDDEN_MARKERS)
        allowed = any(marker in license_name for marker in _ALLOWED_MARKERS)
        if forbidden or not allowed:
            rejected.append(f"{record['Name']} {record['Version']}: {record['License']}")
    if rejected:
        raise SystemExit("disallowed or unknown dependency licenses:\n" + "\n".join(rejected))
    print(f"validated {len(records)} installed dependency licenses")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
