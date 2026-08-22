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


def string_value(item: dict[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def extract_sha_from_text(text: str | None) -> str | None:
    if not text:
        return None

    match = re.search(r"<!--\s*upstream_sha:\s*([0-9a-fA-F]{40})\s*-->", text)
    if match:
        return match.group(1).lower()

    match = re.search(r"/commit/([0-9a-fA-F]{40})", text)
    if match:
        return match.group(1).lower()

    match = re.search(r"coreprotect-([0-9a-fA-F]{40})", text)
    if match:
        return match.group(1).lower()

    return None


def load_releases(path: Path, current_release_tag: str | None) -> list[Release]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        return []

    releases: list[Release] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            continue

        tag = string_value(item, "tagName", "tag_name")
        if not tag or tag == current_release_tag:
            continue

        match = TAG_RE.fullmatch(tag)
        sha = match.group(1).lower() if match else extract_sha_from_text(string_value(item, "body"))
        if not sha:
            continue

        date = string_value(item, "publishedAt", "published_at", "createdAt", "created_at") or ""
        releases.append(Release(index=index, tag=tag, sha=sha, date=date))

    return sorted(releases, key=lambda release: (release.date, -release.index), reverse=True)


def get_previous_sha(
    release_json: Path | None,
    previous_sha: str | None,
    releases_json: Path | None = None,
    current_release_tag: str | None = None,
) -> str | None:
    if previous_sha and previous_sha.strip():
        return previous_sha.strip().lower()

    if release_json and release_json.is_file():
        try:
            content = release_json.read_text(encoding="utf-8").strip()
            if content:
                data = json.loads(content)
                if isinstance(data, dict):
                    body = data.get("body") or ""
                    tag = data.get("tagName") or data.get("tag_name") or ""
                    sha = extract_sha_from_text(body) or extract_sha_from_text(tag)
                    if sha:
                        return sha
                elif isinstance(data, list):
                    for item in data:
                        if isinstance(item, dict):
                            body = item.get("body") or ""
                            tag = item.get("tagName") or item.get("tag_name") or ""
                            sha = extract_sha_from_text(body) or extract_sha_from_text(tag)
                            if sha:
                                return sha
        except Exception:
            pass

    if releases_json and releases_json.is_file():
        try:
            releases = load_releases(releases_json, current_release_tag)
            if releases:
                return releases[0].sha
        except Exception:
            pass

    return None


def commit_url(upstream_repo: str, sha: str) -> str:
    return f"https://github.com/{upstream_repo}/commit/{sha}"


def linked_commit(upstream_repo: str, sha: str, short_sha: str | None = None) -> str:
    label = short_sha or sha[:7]
    return f"[`{label}`]({commit_url(upstream_repo, sha)})"


def choose_commits(repo: Path, current_sha: str, previous_sha: str | None) -> tuple[list[Commit], str]:
    if not previous_sha:
        return read_commits(repo, "--max-count=1", current_sha), (
            "首次发布：未找到可比较的上一次自动构建提交；下面仅列出当前构建提交。"
        )

    if previous_sha == current_sha:
        return [], "本次没有新增上游 commit；这是对同一上游 SHA 的重新构建。"

    if not commit_exists(repo, previous_sha):
        return read_commits(repo, "--max-count=1", current_sha), (
            "未能在当前上游分支历史中找到上一次构建的 SHA；"
            "可能是上游历史被改写或比较基准不在当前分支，下面仅列出当前构建提交。"
        )

    if not is_ancestor(repo, previous_sha, current_sha):
        return read_commits(repo, "--max-count=1", current_sha), (
            "上一次构建的 SHA 不是当前上游 SHA 的祖先；"
            "可能是上游历史被改写或切换了分支，下面仅列出当前构建提交。"
        )

    commits = read_commits(repo, f"{previous_sha}..{current_sha}")
    if not commits:
        return [], "本次没有新增上游 commit。"

    return commits, f"以下为自上一次自动构建以来新增的 {len(commits)} 个上游 commit。"


def render_notes(
    upstream_repo: str,
    current_sha: str,
    commits: list[Commit],
    summary: str,
) -> str:
    lines = [
        "## 上游新增补丁",
        "",
        summary,
    ]

    if commits:
        lines.append("")
        for commit in commits:
            lines.append(
                f"- {linked_commit(upstream_repo, commit.sha, commit.short_sha)} "
                f"{commit.subject} - {commit.author}, {commit.date}"
            )

    lines.extend(["", f"<!-- upstream_sha: {current_sha} -->", ""])
    return "\n".join(lines)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coreprotect-dir", required=True, type=Path)
    parser.add_argument("--release-json", type=Path)
    parser.add_argument("--releases-json", type=Path)
    parser.add_argument("--upstream-repo", required=True)
    parser.add_argument("--upstream-branch", default="master")
    parser.add_argument("--current-sha", required=True)
    parser.add_argument("--previous-sha")
    parser.add_argument("--project-branch", default="development")
    parser.add_argument("--current-release-tag")
    parser.add_argument("--output", required=True, type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    current_sha = args.current_sha.lower()

    if not commit_exists(args.coreprotect_dir, current_sha):
        print(f"Current SHA {current_sha} does not exist in {args.coreprotect_dir}", file=sys.stderr)
        return 1

    previous_sha = get_previous_sha(
        release_json=args.release_json,
        previous_sha=args.previous_sha,
        releases_json=args.releases_json,
        current_release_tag=args.current_release_tag,
    )
    commits, summary = choose_commits(args.coreprotect_dir, current_sha, previous_sha)
    notes = render_notes(
        upstream_repo=args.upstream_repo,
        current_sha=current_sha,
        commits=commits,
        summary=summary,
    )

    args.output.write_text(notes, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
