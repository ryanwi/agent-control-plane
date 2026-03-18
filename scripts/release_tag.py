"""Guarded local release tagging helper.

Usage:
    uv run python scripts/release_tag.py --version 0.9.4
"""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")
VERSION_RE = re.compile(r'^version\s*=\s*"([^"]+)"', re.M)


def _run(cmd: list[str]) -> str:
    result = subprocess.run(cmd, check=True, capture_output=True, text=True)
    return result.stdout.strip()


def _assert_clean_worktree() -> None:
    status = _run(["git", "status", "--porcelain"])
    if status:
        raise SystemExit("Working tree is not clean. Commit/stash changes before release-tag.")


def _assert_on_main() -> None:
    branch = _run(["git", "branch", "--show-current"])
    if branch != "main":
        raise SystemExit(f"Release tagging must run on main (current: {branch}).")


def _read_pyproject_version() -> str:
    text = Path("pyproject.toml").read_text(encoding="utf-8")
    match = VERSION_RE.search(text)
    if match is None:
        raise SystemExit("Could not find [project].version in pyproject.toml")
    return match.group(1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--version", required=True, help="Release version without v prefix (e.g. 0.9.4)")
    args = parser.parse_args()

    version = args.version
    if SEMVER_RE.match(version) is None:
        raise SystemExit(f"Invalid version: {version}. Expected x.y.z")

    pyproject_version = _read_pyproject_version()
    if pyproject_version != version:
        raise SystemExit(
            f"Version mismatch: pyproject.toml={pyproject_version}, requested={version}. Bump pyproject first."
        )

    _assert_clean_worktree()
    _assert_on_main()

    print("Running full checks...")
    subprocess.run(["make", "check"], check=True)

    tag = f"v{version}"
    existing_tags = _run(["git", "tag", "--list", tag])
    if existing_tags:
        raise SystemExit(f"Tag already exists: {tag}")

    print(f"Creating and pushing {tag}...")
    subprocess.run(["git", "tag", tag], check=True)
    subprocess.run(["git", "push", "origin", "main"], check=True)
    subprocess.run(["git", "push", "origin", tag], check=True)
    print(f"Release complete: {tag}")


if __name__ == "__main__":
    main()
