#!/usr/bin/env python3
"""Generate GitHub release notes for CoreProtect autobuilds."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


TAG_RE = re.compile(r"^coreprotect-([0-9a-fA-F]{40})(?:-run-\d+)?$")
FIELD_SEPARATOR = "\x1f"


@dataclass(frozen=True)
class Release:
    index: int
    tag: str
    sha: str
    date: str


@dataclass(frozen=True)
class Commit:
    sha: str
    short_sha: str
    author: str
    date: str
    subject: str


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


def parse_commit_line(line: str) -> Commit:
    parts = line.split(FIELD_SEPARATOR, 4)
    if len(parts) != 5:
        raise SystemExit(f"Unexpected git log output: {line!r}")
    return Commit(
        sha=parts[0],
        short_sha=parts[1],
        author=parts[2],
        date=parts[3],
        subject=parts[4],
    )


def read_commits(repo: Path, *revision_args: str) -> list[Commit]:
    result = run_git(
        repo,
        "log",
        "--reverse",
        "--date=short",
        f"--format=%H{FIELD_SEPARATOR}%h{FIELD_SEPARATOR}%an{FIELD_SEPARATOR}%ad{FIELD_SEPARATOR}%s",
        *revision_args,
    )
    lines = [line for line in result.stdout.splitlines() if line.strip()]
    return [parse_commit_line(line) for line in lines]


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


def string_value(item: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def commit_url(upstream_repo: str, sha: str) -> str:
    return f"https://github.com/{upstream_repo}/commit/{sha}"


def linked_commit(upstream_repo: str, sha: str, short_sha: str | None = None) -> str:
    label = short_sha or sha[:7]
    return f"[`{label}`]({commit_url(upstream_repo, sha)})"


def choose_commits(repo: Path, current_sha: str, previous: Release | None) -> tuple[list[Commit], str]:
    if previous is None:
        return read_commits(repo, "--max-count=1", current_sha), (
            "首次发布：未找到可比较的上一次自动构建 Release；下面仅列出当前构建提交。"
        )

    if previous.sha == current_sha:
        return [], "本次没有新增上游 commit；这是对同一上游 SHA 的重新构建。"

    if not commit_exists(repo, previous.sha):
        return read_commits(repo, "--max-count=1", current_sha), (
            "未能在当前上游分支历史中找到上一次 Release 的 SHA；"
            "可能是上游历史被改写或比较基准不在当前分支，下面仅列出当前构建提交。"
        )

    if not is_ancestor(repo, previous.sha, current_sha):
        return read_commits(repo, "--max-count=1", current_sha), (
            "上一次 Release 的 SHA 不是当前上游 SHA 的祖先；"
            "可能是上游历史被改写或切换了分支，下面仅列出当前构建提交。"
        )

    commits = read_commits(repo, f"{previous.sha}..{current_sha}")
    if not commits:
        return [], "本次没有新增上游 commit。"

    return commits, f"以下为自上一次自动构建以来新增的 {len(commits)} 个上游 commit。"


def render_notes(
    upstream_repo: str,
    upstream_branch: str,
    current_sha: str,
    project_branch: str,
    previous: Release | None,
    commits: list[Commit],
    summary: str,
) -> str:
    current_short_sha = current_sha[:7]
    lines = [
        f"本次构建来源：{linked_commit(upstream_repo, current_sha, current_short_sha)}",
        "",
        f"- 上游仓库：`{upstream_repo}`",
        f"- 上游分支：`{upstream_branch}`",
        f"- 上游提交：{linked_commit(upstream_repo, current_sha, current_short_sha)}",
        f"- project.branch：`{project_branch}`",
    ]

    if previous is None:
        lines.append("- 上一次自动构建：未找到")
    else:
        lines.append(
            f"- 上一次自动构建：`{previous.tag}` "
            f"({linked_commit(upstream_repo, previous.sha)})"
        )

    lines.extend(["", "## 上游新增补丁", "", summary])

    if commits:
        lines.append("")
        for commit in commits:
            lines.append(
                f"- {linked_commit(upstream_repo, commit.sha, commit.short_sha)} "
                f"{commit.subject} - {commit.author}, {commit.date}"
            )

    lines.append("")
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coreprotect-dir", required=True, type=Path)
    parser.add_argument("--releases-json", required=True, type=Path)
    parser.add_argument("--upstream-repo", required=True)
    parser.add_argument("--upstream-branch", required=True)
    parser.add_argument("--current-sha", required=True)
    parser.add_argument("--project-branch", required=True)
    parser.add_argument("--current-release-tag")
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    current_sha = args.current_sha.lower()

    if not commit_exists(args.coreprotect_dir, current_sha):
        print(f"Current SHA {current_sha} does not exist in {args.coreprotect_dir}", file=sys.stderr)
        return 1

    releases = load_releases(args.releases_json, args.current_release_tag)
    previous = releases[0] if releases else None
    commits, summary = choose_commits(args.coreprotect_dir, current_sha, previous)
    notes = render_notes(
        upstream_repo=args.upstream_repo,
        upstream_branch=args.upstream_branch,
        current_sha=current_sha,
        project_branch=args.project_branch,
        previous=previous,
        commits=commits,
        summary=summary,
    )

    args.output.write_text(notes, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
