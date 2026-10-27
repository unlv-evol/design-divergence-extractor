"""Stage 3: RefactoringMiner runs, their cache, and two-sided rename maps.

RefactoringMiner is invoked as an external process in its ``-bc`` (between
commits) form, which needs no network and fails loudly on a missing commit.
Output is cached per variant pair; a cached run ``start..E`` also answers any
``start..end`` where ``end`` is an ancestor of ``E``, by keeping only the
commits in ``start..end``. That matters because every case of a pair shares
the ancestor, and long histories (Kafka) take a long time to analyse.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dde.git import Repo
from dde.models import RenameMaps

DEFAULT_TIMEOUT = 3600

CLASS_RENAMES = frozenset({"Rename Class", "Move Class", "Move And Rename Class"})
METHOD_RENAMES = frozenset({"Rename Method", "Move And Rename Method"})
FIELD_RENAMES = frozenset({"Rename Attribute"})
RENAME_TYPES = CLASS_RENAMES | METHOD_RENAMES | FIELD_RENAMES

_IN_CLASS = re.compile(r"in class ([\w.$]+)\s*$")
_FROM_CLASS = re.compile(r"from class ([\w.$]+)\s*$")


class RefactoringMinerError(RuntimeError):
    pass


@dataclass(frozen=True)
class Location:
    file_path: str
    element_type: str
    element: str
    start_line: int
    end_line: int


@dataclass(frozen=True)
class Refactoring:
    side: str  # "source" | "target"
    commit: str
    order: int  # position of the commit in ancestor..end, oldest first
    type: str
    description: str
    left: tuple[Location, ...]
    right: tuple[Location, ...]

    @property
    def files(self) -> set[str]:
        return {loc.file_path for loc in (*self.left, *self.right)}


# -- running -----------------------------------------------------------------------


def run_range(
    executable: Path,
    repo: Repo,
    start: str,
    end: str,
    cache_dir: Path,
    timeout: int = DEFAULT_TIMEOUT,
) -> list[dict[str, Any]]:
    """RefactoringMiner commits for ``start..end``, from cache when possible."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    exact = cache_dir / f"{start}__{end}.json"
    if exact.is_file():
        return list(json.loads(exact.read_text(encoding="utf-8"))["commits"])
    for cached in sorted(cache_dir.glob(f"{start}__*.json")):
        wider_end = cached.stem.split("__", 1)[1]
        if repo.is_ancestor(end, wider_end):
            keep = set(repo.rev_list(start, end))
            commits = json.loads(cached.read_text(encoding="utf-8"))["commits"]
            return [c for c in commits if c.get("sha1") in keep]
    if start == end:
        return []
    commits = _invoke(executable, repo, start, end, timeout)
    tmp = exact.with_suffix(".tmp")
    tmp.write_text(
        json.dumps({"start": start, "end": end, "commits": commits}, sort_keys=True),
        encoding="utf-8",
    )
    tmp.replace(exact)
    return commits


def _invoke(executable: Path, repo: Repo, start: str, end: str, timeout: int) -> list[dict[str, Any]]:
    if not executable.exists():
        raise RefactoringMinerError(f"RefactoringMiner not found at {executable}")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "rm.json"
        cmd = [str(executable), "-bc", str(repo.path.resolve()), start, end, "-json", str(out)]
        try:
            proc = subprocess.run(  # noqa: S603 - configured path, no shell
                cmd, capture_output=True, text=True, timeout=timeout, check=False
            )
        except subprocess.TimeoutExpired as exc:
            raise RefactoringMinerError(f"timed out after {timeout}s") from exc
        except OSError as exc:
            raise RefactoringMinerError(f"could not run: {exc}") from exc
        if proc.returncode != 0:
            last = (proc.stderr.strip().splitlines() or [""])[-1]
            raise RefactoringMinerError(f"exit {proc.returncode}: {last[:300]}")
        if not out.is_file():
            raise RefactoringMinerError("no output file written")
        try:
            report = json.loads(out.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise RefactoringMinerError(f"unreadable output: {exc}") from exc
    return list(report.get("commits") or [])


def version_of(executable: Path | None) -> str:
    if executable is None:
        return "not configured"
    for part in executable.resolve().parts:
        m = re.fullmatch(r"RefactoringMiner-([\d.]+)", part)
        if m:
            return m.group(1)
    vfile = executable.parent / "VERSION"
    return vfile.read_text().strip() if vfile.is_file() else "unknown"


# -- normalizing -------------------------------------------------------------------


def normalize(commits: list[dict[str, Any]], side: str, order: list[str]) -> list[Refactoring]:
    """Flatten RefactoringMiner JSON, ordered oldest commit first."""
    rank = {sha: i for i, sha in enumerate(order)}
    out: list[Refactoring] = []
    for c in commits:
        sha = str(c.get("sha1", ""))
        for r in c.get("refactorings") or []:
            out.append(
                Refactoring(
                    side=side,
                    commit=sha,
                    order=rank.get(sha, len(rank)),
                    type=str(r.get("type", "")),
                    description=str(r.get("description", "")),
                    left=tuple(_loc(x) for x in r.get("leftSideLocations") or []),
                    right=tuple(_loc(x) for x in r.get("rightSideLocations") or []),
                )
            )
    out.sort(key=lambda r: (r.order, r.commit, r.type, r.description))
    return out


def _loc(d: dict[str, Any]) -> Location:
    return Location(
        file_path=str(d.get("filePath", "")),
        element_type=str(d.get("codeElementType", "")),
        element=str(d.get("codeElement", "")),
        start_line=int(d.get("startLine", 0) or 0),
        end_line=int(d.get("endLine", 0) or 0),
    )


def parse_method_element(element: str) -> tuple[str, int] | None:
    """``public flush(timeout int) : void`` -> ``("flush", 1)``."""
    m = re.search(r"([\w$]+)\((.*)\)\s*(:\s*.*)?$", element.strip())
    if not m:
        return None
    params = m.group(2).strip()
    if not params:
        return m.group(1), 0
    depth, count = 0, 1
    for ch in params:
        if ch == "<":
            depth += 1
        elif ch == ">":
            depth -= 1
        elif ch == "," and depth == 0:
            count += 1
    return m.group(1), count


def _first(locs: tuple[Location, ...], element_type: str) -> Location | None:
    return next((loc for loc in locs if loc.element_type == element_type), None)


# -- rename maps -------------------------------------------------------------------


class _Chain:
    """Current name -> ancestor name, followed through successive renames.

    A name that two different originals end up claiming is ambiguous and is
    dropped (spec, pipeline stage 3).
    """

    def __init__(self) -> None:
        self.state: dict[Any, Any] = {}
        self.ambiguous: set[Any] = set()

    def rename(self, old: Any, new: Any) -> None:
        if old == new:
            return
        anc = self.state.pop(old, old)
        if new in self.state and self.state[new] != anc:
            self.ambiguous.add(new)
        self.state[new] = anc

    def resolve(self, current: Any) -> Any:
        return self.state.get(current, current)

    def mapping(self) -> dict[Any, Any]:
        return {k: v for k, v in self.state.items() if k not in self.ambiguous and k != v}


def build_rename_maps(refactorings: list[Refactoring]) -> tuple[RenameMaps, set[int]]:
    """Rename maps for one side, plus the indices of the refactorings used.

    Only one-to-one renames count. Move Method without a name change is left
    out on purpose: it changes the owning class, which I3 measures.
    """
    classes, files, methods, fields, flat = _Chain(), _Chain(), _Chain(), _Chain(), _Chain()
    used: set[int] = set()
    by_commit: dict[int, list[tuple[int, Refactoring]]] = {}
    for i, r in enumerate(refactorings):
        by_commit.setdefault(r.order, []).append((i, r))
    for order in sorted(by_commit):
        batch = by_commit[order]
        for i, r in batch:  # classes first: member renames name the new class
            if r.type in CLASS_RENAMES:
                left, right = _first(r.left, "TYPE_DECLARATION"), _first(r.right, "TYPE_DECLARATION")
                if left and right and left.element and right.element:
                    classes.rename(left.element, right.element)
                    if left.file_path != right.file_path:
                        files.rename(left.file_path, right.file_path)
                    used.add(i)
        for i, r in batch:
            if r.type in METHOD_RENAMES:
                left, right = _first(r.left, "METHOD_DECLARATION"), _first(r.right, "METHOD_DECLARATION")
                lp = parse_method_element(left.element) if left else None
                rp = parse_method_element(right.element) if right else None
                cls_m = _IN_CLASS.search(r.description) or _FROM_CLASS.search(r.description)
                if lp and rp and cls_m and lp[1] == rp[1]:
                    anc_cls = classes.resolve(cls_m.group(1))
                    methods.rename((anc_cls, lp[0], lp[1]), (anc_cls, rp[0], rp[1]))
                    flat.rename(lp[0], rp[0])
                    used.add(i)
            elif r.type in FIELD_RENAMES:
                left, right = _first(r.left, "FIELD_DECLARATION"), _first(r.right, "FIELD_DECLARATION")
                cls_m = _IN_CLASS.search(r.description)
                if left and right and cls_m:
                    anc_cls = classes.resolve(cls_m.group(1))
                    ln = left.element.split(":", 1)[0].strip()
                    rn = right.element.split(":", 1)[0].strip()
                    fields.rename((anc_cls, ln), (anc_cls, rn))
                    used.add(i)
    class_map = classes.mapping()
    simple: dict[str, str] = {}
    simple_dup: set[str] = set()
    for cur, anc in class_map.items():
        s_cur, s_anc = cur.rsplit(".", 1)[-1], anc.rsplit(".", 1)[-1]
        if s_cur == s_anc:
            continue
        if s_cur in simple and simple[s_cur] != s_anc:
            simple_dup.add(s_cur)
        simple[s_cur] = s_anc
    maps = RenameMaps(
        available=True,
        classes=class_map,
        simple_classes={k: v for k, v in simple.items() if k not in simple_dup},
        files=files.mapping(),
        methods={(k[0], k[1], k[2]): v[1] for k, v in methods.mapping().items()},
        flat_methods=flat.mapping(),
        fields={(k[0], k[1]): v[1] for k, v in fields.mapping().items()},
    )
    return maps, used


def counts_touching(refactorings: list[Refactoring], paths: set[str]) -> Counter[str]:
    """Refactorings by type with any location in ``paths`` (baseline covariate)."""
    return Counter(r.type for r in refactorings if r.files & paths)
