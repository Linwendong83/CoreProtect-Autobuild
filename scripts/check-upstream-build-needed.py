#!/usr/bin/env python3
"""Decide whether an upstream CoreProtect change needs a new build."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


TAG_RE = re.compile(r"^coreprotect-([0-9a-fA-F]{40})(?:-run-\d+)?$")
IGNORED_ROOT_NAMES = {
    ".editorconfig",
    ".gitattributes",
    ".gitignore",
    "code_of_conduct.md",
    "contributing.md",
    "license",
    "license.md",
    "security.md",
}
IGNORED_PREFIXES = ("readme", "license")
IGNORED_DIRS = (".github/", "docs/")


@dataclass(frozen=True)
class Release:
    index: int
    tag: str
    sha: str
    date: str


def run_git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and result.returncode != 0:
        command = " ".join(["git", "-C", str(repo), *args])
        raise SystemExit(f"{command} failed:\n{result.stderr.strip()}")
    return result


def commit_exists(repo: Path, sha: str) -> bool:
    return run_git(repo, "cat-file", "-e", f"{sha}^{{commit}}", check=False).returncode == 0


def is_ancestor(repo: Path, ancestor: str, descendant: str) -> bool:
    return run_git(repo, "merge-base", "--is-ancestor", ancestor, descendant, check=False).returncode == 0


def string_value(item: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def load_releases(path: Path, current_release_tag: str | None) -> list[Release]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise SystemExit("Release JSON must be an array")

    releases: list[Release] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue

        tag = string_value(item, "tagName", "tag_name")
        if not tag or tag == current_release_tag:
            continue

        match = TAG_RE.fullmatch(tag)
        if not match:
            continue

        date = string_value(item, "publishedAt", "published_at", "createdAt", "created_at") or ""
        releases.append(Release(index=index, tag=tag, sha=match.group(1).lower(), date=date))

    return sorted(releases, key=lambda release: (release.date, -release.index), reverse=True)


def changed_files(repo: Path, old_sha: str, new_sha: str) -> list[str]:
    result = run_git(repo, "diff", "--name-only", "--diff-filter=ACMRT", f"{old_sha}..{new_sha}")
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def is_ignored_path(path: str) -> bool:
    normalized = path.replace("\\", "/").lower()
    name = normalized.rsplit("/", 1)[-1]

    if normalized.startswith(IGNORED_DIRS):
        return True

    if "/" not in normalized:
        if name in IGNORED_ROOT_NAMES:
            return True
        return any(name.startswith(prefix) for prefix in IGNORED_PREFIXES)

    return False


def summarize(paths: list[str], limit: int = 8) -> str:
    if not paths:
        return "none"
    shown = ", ".join(paths[:limit])
    if len(paths) > limit:
        shown += f", and {len(paths) - limit} more"
    return shown


def write_outputs(path: Path | None, outputs: dict[str, str]) -> None:
    for key, value in outputs.items():
        print(f"{key}={value}")

    if path is None:
        return

    with path.open("a", encoding="utf-8") as output:
        for key, value in outputs.items():
            output.write(f"{key}={value}\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coreprotect-dir", required=True, type=Path)
    parser.add_argument("--releases-json", required=True, type=Path)
    parser.add_argument("--current-sha", required=True)
    parser.add_argument("--current-release-tag")
    parser.add_argument("--force-build", default="false")
    parser.add_argument("--github-output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    current_sha = args.current_sha.lower()

    if args.force_build.lower() == "true":
        write_outputs(args.github_output, {
            "should_build": "true",
            "reason": "Forced build requested.",
        })
        return 0

    if not commit_exists(args.coreprotect_dir, current_sha):
        raise SystemExit(f"Current SHA {current_sha} does not exist in {args.coreprotect_dir}")

    releases = load_releases(args.releases_json, args.current_release_tag)
    previous = releases[0] if releases else None
    if previous is None:
        write_outputs(args.github_output, {
            "should_build": "true",
            "reason": "No previous autobuild release found.",
        })
        return 0

    if previous.sha == current_sha:
        write_outputs(args.github_output, {
            "should_build": "false",
            "reason": f"Latest autobuild release already targets {current_sha[:7]}.",
        })
        return 0

    if not commit_exists(args.coreprotect_dir, previous.sha):
        write_outputs(args.github_output, {
            "should_build": "true",
            "reason": f"Previous release commit {previous.sha[:7]} is not in the current branch history.",
        })
        return 0

    if not is_ancestor(args.coreprotect_dir, previous.sha, current_sha):
        write_outputs(args.github_output, {
            "should_build": "true",
            "reason": f"Previous release commit {previous.sha[:7]} is not an ancestor of {current_sha[:7]}.",
        })
        return 0

    paths = changed_files(args.coreprotect_dir, previous.sha, current_sha)
    build_paths = [path for path in paths if not is_ignored_path(path)]
    if build_paths:
        write_outputs(args.github_output, {
            "should_build": "true",
            "reason": f"Build-impacting upstream files changed: {summarize(build_paths)}",
        })
        return 0

    write_outputs(args.github_output, {
        "should_build": "false",
        "reason": f"Only ignored upstream files changed: {summarize(paths)}",
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
