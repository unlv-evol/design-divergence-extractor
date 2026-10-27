"""A thin, read-only wrapper around the ``git`` command line.

Every call pins a SHA; nothing here reads branch names or the working tree, so
results depend only on the commits named (R4, R5).
"""

from __future__ import annotations

import os
import subprocess
from functools import lru_cache
from pathlib import Path

_ENV = {
    **os.environ,
    "LC_ALL": "C",
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_TERMINAL_PROMPT": "0",
}
_BASE = ("git", "-c", "core.quotepath=off", "-c", "diff.renames=false", "-c", "color.ui=never")


class GitError(RuntimeError):
    pass


class Repo:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._blobs: dict[tuple[str, str], str | None] = {}

    # -- plumbing ---------------------------------------------------------------
    def run(self, *args: str, ok_codes: tuple[int, ...] = (0,)) -> str:
        proc = subprocess.run(  # noqa: S603 - fixed executable, no shell
            [*_BASE, "-C", str(self.path), *args],
            capture_output=True,
            env=_ENV,
            check=False,
        )
        if proc.returncode not in ok_codes:
            err = proc.stderr.decode("utf-8", "replace").strip()
            raise GitError(f"git {' '.join(args[:3])}… failed ({proc.returncode}): {err[:300]}")
        return proc.stdout.decode("utf-8", "replace")

    def exists(self) -> bool:
        try:
            self.run("rev-parse", "--git-dir")
            return True
        except (GitError, FileNotFoundError, NotADirectoryError):
            return False

    # -- commits -------------------------------------------------------------------
    def has_commit(self, sha: str) -> bool:
        if not sha:
            return False
        try:
            self.run("cat-file", "-e", f"{sha}^{{commit}}")
            return True
        except GitError:
            return False

    def resolve(self, sha: str) -> str:
        return self.run("rev-parse", "--verify", f"{sha}^{{commit}}").strip()

    def merge_base(self, a: str, b: str) -> str | None:
        out = self.run("merge-base", a, b, ok_codes=(0, 1)).strip()
        return out or None

    def is_ancestor(self, a: str, b: str) -> bool:
        proc = subprocess.run(  # noqa: S603
            [*_BASE, "-C", str(self.path), "merge-base", "--is-ancestor", a, b],
            capture_output=True,
            env=_ENV,
            check=False,
        )
        return proc.returncode == 0

    def commit_time(self, sha: str) -> int:
        return int(self.run("show", "-s", "--format=%ct", sha).strip())

    def rev_list(self, start: str, end: str) -> list[str]:
        """Commits in ``start..end``, oldest first."""
        out = self.run("rev-list", "--reverse", "--topo-order", f"{start}..{end}")
        return out.split()

    # -- trees and files -----------------------------------------------------------
    def read_file(self, sha: str, path: str) -> str | None:
        key = (sha, path)
        if key not in self._blobs:
            try:
                self._blobs[key] = self.run("show", f"{sha}:{path}")
            except GitError:
                self._blobs[key] = None
        return self._blobs[key]

    def read_files(self, sha: str, paths: list[str]) -> dict[str, str | None]:
        """Read many files at one commit with a single ``cat-file --batch``."""
        todo = [p for p in paths if (sha, p) not in self._blobs]
        if todo:
            request = "".join(f"{sha}:{p}\n" for p in todo).encode()
            proc = subprocess.run(  # noqa: S603
                [*_BASE, "-C", str(self.path), "cat-file", "--batch"],
                input=request,
                capture_output=True,
                env=_ENV,
                check=False,
            )
            if proc.returncode != 0:
                raise GitError(proc.stderr.decode("utf-8", "replace")[:300])
            data = proc.stdout
            pos = 0
            for p in todo:
                nl = data.index(b"\n", pos)
                header = data[pos:nl].decode()
                pos = nl + 1
                if header.endswith(" missing") or header.endswith(" ambiguous"):
                    self._blobs[(sha, p)] = None
                    continue
                size = int(header.split()[-1])
                self._blobs[(sha, p)] = data[pos : pos + size].decode("utf-8", "replace")
                pos += size + 1
        return {p: self._blobs[(sha, p)] for p in paths}

    def file_exists(self, sha: str, path: str) -> bool:
        return path in self.ls_files(sha)

    def ls_files(self, sha: str) -> frozenset[str]:
        return _ls_files(self.path, sha)

    def changed_files(self, a: str, b: str) -> list[str]:
        return sorted(self.run("diff", "--name-only", "--no-renames", a, b).split("\n")[:-1])

    def diff(self, a: str, b: str, paths: list[str] | None = None) -> str:
        args = ["diff", "--no-renames", "--no-ext-diff", "-U3", a, b]
        if paths:
            args += ["--", *paths]
        return self.run(*args)

    def grep_files(self, sha: str, fixed: str, pathspec: str = "*.java") -> list[str]:
        """Files at ``sha`` containing the literal text ``fixed``."""
        out = self.run("grep", "-l", "-F", "-e", fixed, sha, "--", pathspec, ok_codes=(0, 1))
        prefix = f"{sha}:"
        return sorted(line[len(prefix) :] for line in out.splitlines() if line.startswith(prefix))

    def grep_regex_files(self, sha: str, pattern: str, pathspec: str = "*.java") -> list[str]:
        out = self.run("grep", "-l", "-E", "-e", pattern, sha, "--", pathspec, ok_codes=(0, 1))
        prefix = f"{sha}:"
        return sorted(line[len(prefix) :] for line in out.splitlines() if line.startswith(prefix))


@lru_cache(maxsize=64)
def _ls_files(path: Path, sha: str) -> frozenset[str]:
    out = Repo(path).run("ls-tree", "-r", "--name-only", sha)
    return frozenset(out.splitlines())


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
