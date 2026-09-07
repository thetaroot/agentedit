# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 thetaroot

"""Git integration: what changed in the working tree."""
from __future__ import annotations

import subprocess


def repo_root(start: str) -> str | None:
    proc = subprocess.run(
        ["git", "-C", start, "rev-parse", "--show-toplevel"],
        capture_output=True, text=True,
    )
    return proc.stdout.strip() if proc.returncode == 0 else None


def changed_files(repo: str, *, include_untracked: bool = True) -> list[str]:
    """Repo-relative paths changed vs HEAD (tracked diff + untracked)."""
    paths: set[str] = set()

    proc = subprocess.run(
        ["git", "-C", repo, "diff", "--name-only", "HEAD"],
        capture_output=True, text=True,
    )
    if proc.returncode == 0:
        paths.update(p for p in proc.stdout.splitlines() if p)

    if include_untracked:
        proc = subprocess.run(
            ["git", "-C", repo, "ls-files", "--others", "--exclude-standard"],
            capture_output=True, text=True,
        )
        if proc.returncode == 0:
            paths.update(p for p in proc.stdout.splitlines() if p)

    return sorted(paths)


def head_sha(repo: str) -> str:
    proc = subprocess.run(
        ["git", "-C", repo, "rev-parse", "HEAD"],
        capture_output=True, text=True,
    )
    return proc.stdout.strip() if proc.returncode == 0 else ""
