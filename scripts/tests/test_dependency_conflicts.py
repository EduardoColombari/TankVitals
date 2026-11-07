"""Fail when composed backend requirement files pin one package differently."""

from __future__ import annotations

import re
import subprocess
from collections import defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
FILES = ("backend/requirements.txt", "backend/requirements-iot.txt", "backend/plat-requirements.txt")
PIN = re.compile(r"^\s*([A-Za-z0-9_.-]+)\s*===?\s*([^\s;#]+)")


def find_conflicts() -> dict[str, dict[str, set[str]]]:
    pins: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for relative in FILES:
        for line in (ROOT / relative).read_text(encoding="utf-8").splitlines():
            match = PIN.match(line)
            if match:
                pins[match.group(1).lower()][match.group(2)].add(relative)
    return {name: versions for name, versions in pins.items() if len(versions) > 1}


def find_remote_conflicts() -> tuple[dict[str, dict[str, set[str]]], str | None]:
    pins: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for relative in FILES:
        result = subprocess.run(
            ["git", "show", f"origin/main:{relative}"], cwd=ROOT,
            capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            return {}, "could not read origin/main requirement files; remote dependency check is incomplete"
        for line in result.stdout.splitlines():
            match = PIN.match(line)
            if match:
                pins[match.group(1).lower()][match.group(2)].add(f"origin/main:{relative}")
    return {name: versions for name, versions in pins.items() if len(versions) > 1}, None


def show(conflicts: dict[str, dict[str, set[str]]]) -> None:
    if conflicts:
        for package, versions in sorted(conflicts.items()):
            detail = ", ".join(f"{version} ({', '.join(sorted(files))})" for version, files in sorted(versions.items()))
            print(f"FAIL: conflicting pins for {package}: {detail}")
    else:
        print("PASS: no conflicting exact pins in the checked requirement files")


if __name__ == "__main__":
    local = find_conflicts()
    show(local)
    remote, warning = find_remote_conflicts()
    if warning:
        print(f"WARN: {warning}")
    else:
        show(remote)
    raise SystemExit(1 if local or remote else (2 if warning else 0))
