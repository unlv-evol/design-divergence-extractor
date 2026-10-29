"""Per-pair working repositories (spec Section 4): the only network step."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from dde.git import GitError

_ENV = {**os.environ, "LC_ALL": "C", "GIT_TERMINAL_PROMPT": "0"}


def setup_pair_repo(dest: Path, source_url: str, target_url: str) -> list[str]:
    """Create the per-pair working repository (spec Section 4).

    The source is cloned without a checkout, the target added as remote
    ``target``, and pull-request heads fetched where the host exposes them. This
    is the only network step in the tool.
    """
    log: list[str] = []

    def git(*args: str, cwd: Path | None = None, ok: tuple[int, ...] = (0,)) -> None:
        proc = subprocess.run(  # noqa: S603
            ["git", *args], cwd=cwd, capture_output=True, env=_ENV, check=False
        )
        log.append(f"git {' '.join(args)} -> {proc.returncode}")
        if proc.returncode not in ok:
            raise GitError(proc.stderr.decode("utf-8", "replace")[:300])

    if not (dest / ".git").exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        git("clone", "--no-checkout", "--origin", "source", source_url, str(dest))
    else:
        git("fetch", "source", cwd=dest)
    git("remote", "add", "target", target_url, cwd=dest, ok=(0, 3))
    git("fetch", "--tags", "target", cwd=dest)
    for remote in ("source", "target"):
        git(
            "fetch",
            remote,
            f"+refs/pull/*/head:refs/remotes/{remote}/pull/*",
            cwd=dest,
            ok=(0, 1, 128),
        )
    return log
